"""``PolicyDecision`` - typed policy-decision row for evidence packets
(W279 directive).

Policy decisions are the *evaluative* axis of an evidence packet: for
every rule / gate / authority object that fired during a change scope,
the packet records one row naming the rule, the verdict it produced,
and any supporting context (subject acted on, evidence pointer,
producer-specific detail). Before W279, ``ChangeEvidence.policy_decisions``
was typed as ``tuple[Mapping[str, Any], ...]`` and producers hand-built
dicts; this module promotes the row shape to a frozen dataclass so the
contract is explicit and a closed-enum validation guards the ``decision``
literal against silent producer drift (e.g. a future ``decision="approved"``
typo that the dict shape would let through).

The dataclass deliberately mirrors :class:`roam.evidence.approval.ApprovalRecord`
in style:

* Frozen so a decision row can be hashed and used inside content-hashed
  evidence packets.
* No mutable default for ``extra`` (uses
  ``dataclasses.field(default_factory=dict)``).
* Validation in ``__post_init__`` for the few invariants we can check
  cheaply (non-empty ``rule_id``, ``decision`` is in the closed
  :data:`roam.evidence._vocabulary.POLICY_DECISIONS` set).

W279 wire-format stability requirement
--------------------------------------
The dataclass MUST round-trip the existing on-wire shape *byte-for-byte*
so stored ``ChangeEvidence.content_hash`` values stay valid across the
schema-v0 -> schema-v0-with-typed-policy-decisions transition. That
means:

* :meth:`PolicyDecision.from_dict` accepts a row whose ``rule_id`` +
  ``decision`` keys are populated. Other recognised top-level keys
  (``subject``, ``subject_kind``, ``evidence_ref``) lift to first-class
  fields; everything else gets stuffed into ``extra``.
* :meth:`PolicyDecision.to_dict` flattens ``extra`` back into the
  top-level dict and OMITS fields whose value is the per-field default
  (``None`` scalars and the empty ``extra`` dict). The resulting bytes
  match what producers emit today.
* Non-conforming dict rows (missing ``rule_id`` and/or ``decision``) are
  left untouched as raw ``Mapping`` instances by the
  :class:`roam.evidence.change_evidence.ChangeEvidence` constructor.
  This preserves byte-stability for hand-crafted golden fixtures whose
  rows don't follow the modern producer convention (e.g. the
  ``v0_full`` fixture's ``{"decision": "allow", "rule":
  "no_unguarded_io"}`` row uses ``rule`` rather than ``rule_id``).

NON-GOALS:

* No raw rule source / clause text. Producers reference rules by id;
  the full clause body lives in ``.roam/rules.yml`` (or the equivalent
  config source) and is read by the consumer if needed.
* No human-readable rationale narrative. Short ``reason`` strings live
  in ``extra`` when a producer emits them; long-form explanations
  belong in an ``EvidenceArtifact(kind="report", ...)``.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any

from roam.evidence._vocabulary import POLICY_DECISIONS


# Top-level row keys that lift to first-class fields on the dataclass.
# Anything else found in a producer row gets stuffed into ``extra`` so
# the row round-trips byte-identically.
_FIRST_CLASS_FIELDS: frozenset[str] = frozenset({
    "rule_id",
    "decision",
    "subject",
    "subject_kind",
    "evidence_ref",
})


@dataclasses.dataclass(frozen=True)
class PolicyDecision(Mapping[str, Any]):
    """First-class policy-decision row per W279 directive.

    Subclasses :class:`collections.abc.Mapping` (read-only) so that
    pre-W279 consumers that hold a row via
    ``packet.policy_decisions[i]`` and do ``isinstance(row, Mapping)``
    + dict-style subscripting keep working unchanged. Notably,
    :func:`roam.evidence.profiles._redact_mapping_tuple` skips any
    entry that fails ``isinstance(row, Mapping)``; without this base
    class, redaction silently bypassed typed PolicyDecision rows
    while still scrubbing legacy dict rows - the asymmetry would have
    been a latent privacy leak. The instance stays frozen
    (no ``__setitem__``); ``dict(pd)`` returns the canonical flattened
    view via :meth:`to_dict`.

    Fields:

    * ``rule_id`` - canonical rule / gate / authority identifier. Free-
      form string by convention prefixed with the source domain
      (``"constitution:<gate>"``, ``"permit:<id>"``, ``"lease:<id>"``,
      ``"audit_trail_chain_integrity"``, ``"<rule.name>"``). Stored
      verbatim; consumers do not parse the inside.
    * ``decision`` - one of :data:`roam.evidence._vocabulary.POLICY_DECISIONS`.
      Validated at construction time; an unknown literal raises
      ``ValueError`` so producer drift surfaces immediately rather
      than silently leaking into the wire format.
    * ``subject`` - optional subject the decision is about. Type is
      deliberately ``Any`` because today's producers emit both string
      identifiers and list payloads (the lease gatherer emits a list of
      lease-subject strings).
    * ``subject_kind`` - optional ``SUBJECT_KINDS`` literal naming the
      kind of the subject. Closed-enum validation is NOT enforced here
      because the subject axis can also be ``None`` and the kind
      vocabulary is broader than the evidence-subject vocabulary in
      some legacy producer paths; consumers that need closed-enum
      validation should call into :func:`roam.evidence._vocabulary`
      explicitly.
    * ``evidence_ref`` - optional pointer to a supporting artifact /
      finding / rule clause. By convention prefixed with the kind of
      the referent (``"rule:<id>"``, ``"artifact:<id>"``,
      ``"constitution:<gate>"``).
    * ``extra`` - free-form structured detail (severity, reason,
      command_count, expires_at, scope, state, issue_kind,
      entry_index, ...). Producers populate the keys they need;
      :meth:`to_dict` flattens them into the top-level wire shape
      to preserve byte-stability with pre-W279 dict-only rows.

    NON-GOAL: this dataclass does not store raw rule bodies, full clause
    text, or long-form narrative reasoning (those belong in
    ``.roam/rules.yml``, source-controlled policy files, or an
    ``EvidenceArtifact(kind="report", ...)``).
    """

    rule_id: str
    decision: str
    subject: Any = None
    subject_kind: str | None = None
    evidence_ref: str | None = None
    extra: Mapping[str, Any] = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.rule_id, str) or not self.rule_id:
            raise ValueError(
                "PolicyDecision.rule_id must be a non-empty string"
            )
        if not isinstance(self.decision, str) or not self.decision:
            raise ValueError(
                "PolicyDecision.decision must be a non-empty string"
            )
        if self.decision not in POLICY_DECISIONS:
            raise ValueError(
                f"PolicyDecision.decision {self.decision!r} not in "
                f"POLICY_DECISIONS"
            )
        if self.subject_kind is not None:
            if (
                not isinstance(self.subject_kind, str)
                or not self.subject_kind
            ):
                raise ValueError(
                    "PolicyDecision.subject_kind must be None or a "
                    "non-empty string"
                )
        if self.evidence_ref is not None:
            if (
                not isinstance(self.evidence_ref, str)
                or not self.evidence_ref
            ):
                raise ValueError(
                    "PolicyDecision.evidence_ref must be None or a "
                    "non-empty string"
                )

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> PolicyDecision:
        """Normalise a producer-emitted dict row into a typed instance.

        Requires both ``rule_id`` and ``decision`` to be present + non-
        empty; raises ``ValueError`` otherwise. Recognised first-class
        keys (``subject``, ``subject_kind``, ``evidence_ref``) lift to
        their dataclass slots; every other key goes into ``extra`` so the
        row round-trips byte-identically through :meth:`to_dict`.
        """
        rule_id = row.get("rule_id")
        decision = row.get("decision")
        if not isinstance(rule_id, str) or not rule_id:
            raise ValueError(
                "PolicyDecision.from_dict: row missing non-empty 'rule_id'"
            )
        if not isinstance(decision, str) or not decision:
            raise ValueError(
                "PolicyDecision.from_dict: row missing non-empty 'decision'"
            )
        extra: dict[str, Any] = {
            k: v for k, v in row.items() if k not in _FIRST_CLASS_FIELDS
        }
        return cls(
            rule_id=rule_id,
            decision=decision,
            subject=row.get("subject"),
            subject_kind=row.get("subject_kind"),
            evidence_ref=row.get("evidence_ref"),
            extra=extra,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to the canonical dict shape producers emit today.

        Byte-stability rule: omit fields whose value is the per-field
        default (``None`` scalars + empty ``extra`` dict). ``extra``
        keys are flattened into the top-level dict so the resulting
        bytes match the pre-W279 free-form dict shape.

        The returned dict is a plain ``dict`` (not the frozen Mapping
        of the dataclass) so callers can mutate the copy without
        affecting the dataclass instance.
        """
        out: dict[str, Any] = {
            "rule_id": self.rule_id,
            "decision": self.decision,
        }
        if self.subject is not None:
            # Defensive copy for list payloads (lease gatherer emits a
            # list of subject strings); leave scalar subjects as-is.
            if isinstance(self.subject, list):
                out["subject"] = list(self.subject)
            else:
                out["subject"] = self.subject
        if self.subject_kind is not None:
            out["subject_kind"] = self.subject_kind
        if self.evidence_ref is not None:
            out["evidence_ref"] = self.evidence_ref
        # Flatten extra into the top-level so the wire shape matches
        # what producers emit today (free-form dict with no nested
        # ``extra`` envelope).
        for k, v in self.extra.items():
            # Producer-side keys win iff they would collide with a
            # first-class field; in practice ``from_dict`` filters
            # _FIRST_CLASS_FIELDS out of extra, so the loop below
            # cannot clobber rule_id/decision/subject/subject_kind/
            # evidence_ref. The defensive ``if k not in out`` keeps
            # the contract explicit in case a caller constructs a
            # PolicyDecision with a hand-built ``extra`` that
            # mistakenly carries one of those keys.
            if k not in out:
                out[k] = v
        return out

    # ------------------------------------------------------------------
    # Mapping-style accessors (W279 backward-compat)
    # ------------------------------------------------------------------
    #
    # Pre-W279 ``policy_decisions`` entries were free-form dicts; many
    # existing call sites (tests, the ``cmd_evidence_doctor`` /
    # ``cmd_evidence_diff`` rendering paths, third-party consumers
    # reading the field via ``packet.policy_decisions``) subscript each
    # row as ``row["rule_id"]`` / ``row.get("severity")``. To preserve
    # that public surface without forcing every consumer to refactor to
    # attribute access, ``PolicyDecision`` exposes a Mapping-style
    # facade that reads from the flattened ``to_dict()`` view.
    #
    # This does NOT make the dataclass a full ``MutableMapping``: the
    # instance stays frozen, ``__setitem__`` is not implemented, and
    # ``dict(pd)`` returns the canonical flattened dict via
    # ``to_dict``. The intent is read-only compatibility for the wire
    # shape, not first-class dict semantics.

    def __getitem__(self, key: str) -> Any:
        """Return the value for ``key`` from the canonical dict shape.

        Raises ``KeyError`` for unknown keys, matching dict semantics.
        Looks up against the flattened ``to_dict()`` view so the same
        keys that appear in canonical JSON are visible to consumers.
        """
        view = self.to_dict()
        if key not in view:
            raise KeyError(key)
        return view[key]

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        return key in self.to_dict()

    def __iter__(self):  # type: ignore[no-untyped-def]
        """Iterate the canonical-view keys (``Mapping`` ABC contract)."""
        return iter(self.to_dict())

    def __len__(self) -> int:
        """Return the canonical-view key count (``Mapping`` ABC contract)."""
        return len(self.to_dict())

    # NOTE: ``get`` and ``keys`` are already provided by the
    # ``Mapping`` ABC via ``__getitem__`` + ``__iter__`` + ``__len__``;
    # explicit overrides below preserve the pre-W279b method signatures
    # for any consumer relying on the docstring / direct attribute
    # lookup.

    def get(self, key: str, default: Any = None) -> Any:
        """Mapping-style ``get`` against the canonical flattened view."""
        return self.to_dict().get(key, default)

    def keys(self):  # type: ignore[no-untyped-def]
        """Return the canonical-view keys (for ``dict(pd)`` compatibility)."""
        return self.to_dict().keys()


__all__ = ["PolicyDecision"]
