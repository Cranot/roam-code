"""``roam.evidence`` - Phase 0 vocabulary freeze + Phase 1 schema v0
+ Phase 2 envelope collector for the evidence-compiler (W174 / W176)
+ W182 agentic-assurance refs (identity / authority / environment).

Public API:

* ``ChangeEvidence``       - one evidence packet for one code-change scope
* ``EvidenceSubject``      - portable identity for symbols / files / ...
* ``EvidenceLink``         - typed edge between two subjects
* ``EvidenceArtifact``     - generated artifact (report / SARIF / ...)
* ``ActorRef`` / ``AuthorityRef`` / ``EnvironmentRef`` - W182
  agentic-assurance ref dataclasses appended to ``ChangeEvidence``
* ``collect_change_evidence`` - W176 envelope collector that turns
  existing Roam JSON envelopes into one ``ChangeEvidence`` packet
* ``EVIDENCE_SCHEMA_VERSION`` - schema-version constant stamped on packets
* ``SUBJECT_KINDS`` / ``LINK_KINDS`` / ``ARTIFACT_KINDS`` /
  ``CLAIM_SEVERITIES`` / ``REDACTION_REASONS`` / ``ACTOR_KINDS`` /
  ``AUTHORITY_KINDS`` / ``ENV_KINDS`` - closed-enumeration frozensets
  used for construction-time validation

Phase 0 + 1 deliberately ships as pure dataclasses with no DB
migration; Phase 2 (envelope collector) layers on top.

See ``(internal memo)`` for the full
architecture memo and
``(internal memo)`` for the W182 build
deltas.
"""

from __future__ import annotations

from roam.evidence._vocabulary import (
    ACTOR_KINDS,
    ACTOR_TRUST_TIERS,
    ARTIFACT_KINDS,
    AUTHORITY_KINDS,
    AUTHORITY_SOURCES,
    CLAIM_SEVERITIES,
    ENV_KINDS,
    GITHUB_REVIEW_STATES,
    LINK_KINDS,
    POLICY_DECISIONS,
    PROVENANCE_SOURCES,
    REDACTION_REASONS,
    SUBJECT_KINDS,
)
from roam.evidence.actor_trust import classify_actor_trust_tier
from roam.evidence.approval import ApprovalRecord
from roam.evidence.artifact import (
    INLINE_CONTENT_SOFT_LIMIT_BYTES,
    EvidenceArtifact,
)
from roam.evidence.banner import (
    TIER_INSUFFICIENT,
    TIER_LABELS,
    TIER_PARTIAL,
    TIER_STRONG,
    banner_envelope_block,
    classify_evidence_coverage,
    render_banner_markdown,
)
from roam.evidence.change_evidence import (
    EVIDENCE_SCHEMA_VERSION,
    PACKET_BUDGET_STATES,
    PACKET_SIZE_BUDGET_BYTES,
    ChangeEvidence,
    classify_packet_budget,
    packet_size_bytes,
    stale_accepted_risks,
)
from roam.evidence.collector import collect_change_evidence
from roam.evidence.env_refs import build_environment_refs
from roam.evidence.feedback import (
    DEFAULT_FEEDBACK_DIR,
    FEEDBACK_DECISIONS,
    FREE_TEXT_MAX_CHARS,
    FindingFeedback,
    aggregate_dismissal_reasons,
    load_feedback,
    persist_feedback,
)
from roam.evidence.github_reviews import (
    harvest_reviews_from_gh_cli,
    load_reviews_from_fixture,
    parse_github_reviews,
)
from roam.evidence.link import EvidenceLink
from roam.evidence.mcp_receipt import McpDecisionReceipt, hash_input_args
from roam.evidence.policy import PolicyDecision
from roam.evidence.profiles import EXPORT_PROFILES, ExportProfile, apply_profile
from roam.evidence.provenance import provenance_label
from roam.evidence.refs import ActorRef, AuthorityRef, EnvironmentRef
from roam.evidence.subject import EvidenceSubject

__all__ = [
    # Vocabulary
    "ACTOR_KINDS",
    "ACTOR_TRUST_TIERS",
    "ARTIFACT_KINDS",
    "AUTHORITY_KINDS",
    "AUTHORITY_SOURCES",
    "CLAIM_SEVERITIES",
    "ENV_KINDS",
    "EXPORT_PROFILES",
    "FEEDBACK_DECISIONS",
    "GITHUB_REVIEW_STATES",
    "LINK_KINDS",
    "POLICY_DECISIONS",
    "PROVENANCE_SOURCES",
    "REDACTION_REASONS",
    "SUBJECT_KINDS",
    "TIER_INSUFFICIENT",
    "TIER_LABELS",
    "TIER_PARTIAL",
    "TIER_STRONG",
    # Dataclasses
    "ActorRef",
    "ApprovalRecord",
    "AuthorityRef",
    "ChangeEvidence",
    "EnvironmentRef",
    "EvidenceArtifact",
    "EvidenceLink",
    "EvidenceSubject",
    "ExportProfile",
    "FindingFeedback",
    "McpDecisionReceipt",
    "PolicyDecision",
    # Collector
    "collect_change_evidence",
    # Helpers
    "aggregate_dismissal_reasons",
    "apply_profile",
    "banner_envelope_block",
    "build_environment_refs",
    "classify_actor_trust_tier",
    "classify_evidence_coverage",
    "classify_packet_budget",
    "harvest_reviews_from_gh_cli",
    "hash_input_args",
    "load_feedback",
    "load_reviews_from_fixture",
    "packet_size_bytes",
    "parse_github_reviews",
    "persist_feedback",
    "provenance_label",
    "render_banner_markdown",
    "stale_accepted_risks",
    # Constants
    "DEFAULT_FEEDBACK_DIR",
    "EVIDENCE_SCHEMA_VERSION",
    "FREE_TEXT_MAX_CHARS",
    "INLINE_CONTENT_SOFT_LIMIT_BYTES",
    "PACKET_BUDGET_STATES",
    "PACKET_SIZE_BUDGET_BYTES",
]
