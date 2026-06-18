"""``McpDecisionReceipt`` - local decision receipt for a sensitive MCP
tool call (W183).

Per ``(internal memo)`` §"Build deltas"
item 4 (lines 130-144). Each sensitive MCP tool invocation should
produce one of these so that "who invoked what tool with what args,
and what did the policy layer decide?" is locally verifiable evidence.

This wave delivers the data model only — CLI / ``mcp_server.py``
emission is a follow-up wave. Receipts are local artifacts that will
be bundled into ``ChangeEvidence`` later via the W176 collector path.

Design mirrors ``EvidenceArtifact`` in spirit:

* Frozen dataclass so a receipt can be hashed / used as a dict key.
* Mutually exclusive ``output_ref`` / ``output_hash`` (matches
  ``EvidenceArtifact``'s ``path`` / ``content_inline`` discipline).
* Closed-enumeration validation on ``policy_decision`` and
  ``redactions[]`` at construction time.
* Deterministic JSON serialisation and a stable sha256 content hash.

Historical note: W182 (ActorRef on ChangeEvidence) landed in a sibling
wave. This module references the actor as a plain
``actor_ref_id: str | None`` for compatibility; a follow-up could
tighten the type to the ActorRef dataclass directly.

NON-GOALS:

* No raw tokens. ``McpDecisionReceipt`` never stores credential
  material; identity claims come in as ``actor_ref_id`` (a stable
  string id), never as the token bytes themselves.
* No raw input or output bodies. ``input_hash`` and ``output_hash``
  are sha256 digests; the bytes that produced them belong in
  side-channel logs (under explicit opt-in) and are referenced from
  the receipt by hash only.
* No permissive fallback. ``policy_decision`` defaults to
  ``"not_evaluated"`` (an honest "no policy layer was active") rather
  than ``"allow"`` - we never claim approval that was not actually
  granted.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping
from typing import Any

from roam.evidence._vocabulary import POLICY_DECISIONS, REDACTION_REASONS
from roam.evidence.change_evidence import compute_canonical_json_hash

#: Closed enumeration of policy-layer decisions on an MCP tool call.
#:
#: This is the **authority-gate subset** of the canonical
#: :data:`roam.evidence._vocabulary.POLICY_DECISIONS` (9 verdicts). MCP
#: tool-call decisions span the authority-gate axis (allow / deny /
#: escalate / redact / not_evaluated) plus the MCP-P1.1 shadow-mode
#: marker (would_deny_dry_run); the rule-evaluation verdicts
#: (``pass`` / ``fail`` / ``unknown``) belong to the rules-engine layer
#: and are not produced at the MCP boundary.
#:
#: Derived rather than hand-spelled to prevent Pattern 3a vocabulary
#: drift — when the canonical frozenset grows a new authority-gate
#: verdict, this subset participates automatically via the intersection
#: at module-import time. The membership semantic (subset of canonical)
#: is enforced by the assertion below.
#:
#: * ``allow``               - the call was permitted to run
#: * ``deny``                - the call was refused outright
#: * ``escalate``            - the call required human / higher-tier approval
#: * ``redact``              - the call ran but output was masked
#: * ``not_evaluated``       - no policy layer was active (default)
#: * ``would_deny_dry_run``  - MCP-P1.1 shadow-mode: the gate WOULD have
#:                             denied under ``ROAM_MODE_ENFORCEMENT=1``,
#:                             but ``ROAM_MODE_DRY_RUN=1`` was set so the
#:                             call proceeded for observe-only rollout.
#:                             Receipts carrying this verdict also stamp
#:                             ``extra["shadow_mode"] = True`` +
#:                             ``extra["would_deny_reason"]`` so an
#:                             auditor can see why the steady-state
#:                             policy would have denied.
_POLICY_DECISIONS: frozenset[str] = (
    frozenset(
        {
            "allow",
            "deny",
            "escalate",
            "redact",
            "not_evaluated",
            "would_deny_dry_run",
        }
    )
    & POLICY_DECISIONS
)

# Drift guard: every literal listed above must remain a member of the
# canonical POLICY_DECISIONS. If a future edit drops one of these
# verdicts from the canonical set, this assertion fires at import time
# rather than at first construction of a McpDecisionReceipt with that
# verdict.
assert _POLICY_DECISIONS == {
    "allow",
    "deny",
    "escalate",
    "redact",
    "not_evaluated",
    "would_deny_dry_run",
}, f"_POLICY_DECISIONS drift: expected authority-gate subset of POLICY_DECISIONS, got {sorted(_POLICY_DECISIONS)}"


@dataclasses.dataclass(frozen=True)
class McpDecisionReceipt:
    """Local decision receipt for a sensitive MCP tool call.

    Per AGENTIC-ASSURANCE-CROSSWALK-2026-05-13 §Build delta 4. Each
    sensitive MCP tool invocation should produce one of these so that
    'who invoked what tool with what args, and what did the policy
    layer decide?' is locally verifiable evidence.

    Fields:

    * ``tool_call`` - opaque per-invocation id (caller-generated)
    * ``client_id`` - MCP client process id
    * ``tool_name`` - name of the tool invoked (e.g. ``roam_preflight``)
    * ``actor_ref_id`` - W182 ``ActorRef.actor_id`` when available;
      plain string until W182 lands so the receipt model ships first
    * ``declared_side_effects`` - what the tool declared in
      ``_TOOL_METADATA`` (e.g. ``("read_only",)``,
      ``("write_filesystem",)``)
    * ``required_mode`` - mode the tool requires
      (``read_only`` / ``safe_edit`` / ``migration`` / ``autonomous_pr``)
    * ``input_hash`` - sha256 of canonical-JSON input args; use
      :func:`hash_input_args`
    * ``policy_decision`` - one of :data:`_POLICY_DECISIONS`
    * ``output_ref`` - artifact id or path when the output is large
      (mutually exclusive with ``output_hash``)
    * ``output_hash`` - sha256 of inline output when small (mutually
      exclusive with ``output_ref``)
    * ``run_event_id`` - link to ``.roam/runs/<id>/events.jsonl`` row
    * ``redactions`` - tuple of reasons from
      :data:`roam.evidence._vocabulary.REDACTION_REASONS`
    * ``extra`` - free-form structured detail

    Both ``output_ref`` and ``output_hash`` may be ``None`` (the output
    is not yet known, e.g. the receipt is being constructed pre-call
    for a deny decision).
    """

    tool_call: str
    client_id: str
    tool_name: str
    actor_ref_id: str | None = None
    declared_side_effects: tuple[str, ...] = ()
    required_mode: str | None = None
    input_hash: str | None = None
    policy_decision: str = "not_evaluated"
    output_ref: str | None = None
    output_hash: str | None = None
    run_event_id: str | None = None
    redactions: tuple[str, ...] = ()
    extra: Mapping[str, Any] = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.policy_decision not in _POLICY_DECISIONS:
            raise ValueError(
                f"unknown policy_decision: {self.policy_decision!r}; expected one of {sorted(_POLICY_DECISIONS)}"
            )
        for reason in self.redactions:
            if reason not in REDACTION_REASONS:
                raise ValueError(f"unknown redaction reason: {reason!r}; must be one of REDACTION_REASONS")
        # ``output_ref`` and ``output_hash`` are mutually exclusive —
        # mirrors EvidenceArtifact's path / content_inline discipline.
        # ``output_ref`` carries an artifact pointer (large output
        # stored elsewhere); ``output_hash`` is the hash of the small
        # inline output. Having both creates an ambiguity about which
        # is authoritative.
        if self.output_ref is not None and self.output_hash is not None:
            raise ValueError(
                "output_ref and output_hash are mutually exclusive; "
                "use output_ref for large/referenced outputs, "
                "output_hash for small inline outputs"
            )

    def to_canonical_json(self) -> str:
        """Deterministic JSON: sorted keys, no insignificant whitespace.

        Same JSON dump conventions as :func:`hash_input_args` so a
        receipt's content hash is reproducible across processes and
        Python versions.
        """
        obj = dataclasses.asdict(self)
        return json.dumps(obj, sort_keys=True, separators=(",", ":"))

    def compute_content_hash(self) -> str:
        """sha256 of the canonical-JSON. Used as the receipt's stable id."""
        return compute_canonical_json_hash(self.to_canonical_json())


def hash_input_args(args: Mapping[str, Any]) -> str:
    """Compute sha256 of canonical-JSON of an MCP tool's input args.

    Suitable for the :attr:`McpDecisionReceipt.input_hash` field. Pure
    function: same input → same hash, regardless of caller's dict-key
    insertion order.
    """
    canonical = json.dumps(dict(args), sort_keys=True, separators=(",", ":"))
    return compute_canonical_json_hash(canonical)


__all__ = ["McpDecisionReceipt", "hash_input_args"]
