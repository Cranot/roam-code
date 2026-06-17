"""``ChangeEvidence`` - the headline evidence packet for one code-change
scope.

The architecture memo (lines 76-109) defines the field list; this
module is the dataclass realisation plus the deterministic JSON
serialiser and the content-hash helper. Phase 1 deliberately keeps
this as pure dataclasses with no DB migration - everything lives in
memory and serialises to a canonical JSON form for cross-tool
exchange.

Determinism contract:

* ``to_canonical_json()`` produces byte-stable output regardless of
  Python dict ordering. Round-trip JSON -> parse -> JSON must match
  bytewise.
* ``compute_content_hash()`` is sha256 of the canonical JSON *with the
  ``content_hash`` field zeroed out* (chicken-and-egg avoidance).
* ``with_content_hash()`` returns a new instance carrying the hash so
  the packet is self-describing on the wire.

NON-GOALS:

* No raw tokens. Any authority field that touches credentials (e.g.
  ``AuthorityRef(authority_kind="token_scope")``) MUST carry the
  sha256 hash of the scope, never the token bytes themselves.
* No raw prompts. Agent prompts / system messages / user-typed
  instructions are not stored in the packet; if context capture is
  needed, store the prompt elsewhere (under explicit opt-in) and
  reference by hash via ``EvidenceArtifact``.
* No certification claims. The packet says "maps to control X" or
  "provides evidence for control Y" - it never says "certifies" /
  "makes compliant". The wording lint in Phase 4 enforces this.
* No machine-specific hash drift. Canonical JSON is byte-stable
  across Python versions, dict-ordering modes, and platforms;
  consumers verifying the ``content_hash`` field MUST get the same
  bytes regardless of where they parsed the packet.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import hashlib
import json
from collections.abc import Mapping
from typing import Any

from roam.evidence.approval import _parse_iso
from roam.evidence.artifact import EvidenceArtifact
from roam.evidence.policy import PolicyDecision
from roam.evidence.refs import ActorRef, AuthorityRef, EnvironmentRef
from roam.evidence.subject import EvidenceSubject

# Artifact kinds that can answer Q7 ("what verified it?") without a
# populated tests_run row. Reports/manifests are evidence artifacts, but
# they are not themselves verification.
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


def _has_verification_artifact(artifacts: tuple[EvidenceArtifact, ...]) -> bool:
    return any(getattr(artifact, "kind", None) in _VERIFICATION_ARTIFACT_KINDS for artifact in artifacts)


# Schema version stamped on every packet. Bump on any field-shape
# change; consumers compare against this string and may refuse to
# parse newer versions.
#
# W182 (agentic-assurance crosswalk) adds three optional ref lists
# (``actor_refs``, ``authority_refs``, ``environment_refs``) WITHOUT
# bumping this constant. The reasoning - and the backward-compat
# decision behind it - lives below on the ``ChangeEvidence`` docstring
# under "W182 backward-compat decision". Short version: empty refs are
# OMITTED from the canonical JSON, so v0 packets and v1 (W182) packets
# produce IDENTICAL content_hashes when no refs are populated.
#
# W210 (per-claim confidence + time-aware evidence + stale flag +
# version/config linking + assurance-floor gate + completeness banner)
# adds six optional field-groups on top of W182. To preserve the
# content-hash contract with pre-W210 packets, ALL six additions use
# the same omit-when-default discipline (see
# ``_W210_OMIT_WHEN_DEFAULT_FIELDS`` below). ``schema_version`` stays
# at ``"1.0.0"`` because backward-compat is preserved byte-for-byte for
# any packet that doesn't populate the new fields.
EVIDENCE_SCHEMA_VERSION: str = "1.0.0"

# Field names whose empty-tuple value is skipped from the canonical
# JSON payload to preserve content-hash backward-compatibility with
# pre-W182 packets. See the ``ChangeEvidence`` docstring "W182
# backward-compat decision" section for the full rationale. Only the
# three W182 ref lists qualify - all other tuple fields are emitted
# unconditionally (including when empty) because their absence-as-empty
# is part of the v0 contract that downstream consumers may already
# read.
_W182_OMIT_WHEN_EMPTY_FIELDS: frozenset[str] = frozenset(
    {
        "actor_refs",
        "authority_refs",
        "environment_refs",
    }
)

# Field-name -> default-value-sentinel map for the W210 additions.
# Same backward-compat discipline as ``_W182_OMIT_WHEN_EMPTY_FIELDS``,
# but each W210 field has its own default (some scalar ``None``, one
# boolean ``False``, one empty list) so we drive omission off an
# explicit map rather than a single empty-collection check.
#
# Sentinels are expressed in the POST-canonicalisation shape: tuples
# have already been coerced to lists when we apply this map, so the
# empty-tuple default for ``stale_reasons`` is represented as ``[]``.
#
# Why a per-field allowlist rather than a blanket "omit when falsy"?
# Several pre-W210 fields default to ``None`` (e.g. ``repo_id``,
# ``git_range``, ``verdict``) AND their canonical JSON includes those
# ``null`` entries unconditionally. We must NOT touch them, or every
# stored v0/W182 content_hash breaks. The W210 omit list is therefore
# a strict allowlist by name.
_W210_OMIT_WHEN_DEFAULT_FIELDS: Mapping[str, Any] = {
    # Time-aware evidence (item 2):
    "context_read_at": None,
    "edits_started_at": None,
    "edits_completed_at": None,
    # Stale-evidence detection (item 3):
    "evidence_stale": False,
    "stale_reasons": [],
    # Version + config linking (item 4):
    "roam_version": None,
    "rules_config_hash": None,
    "constitution_hash": None,
    "control_map_hash": None,
}


# ---------------------------------------------------------------------------
# W280 packet-size budget
# ---------------------------------------------------------------------------

# Enforced packet-level size budget for the canonical-JSON
# serialisation of a single ``ChangeEvidence`` packet. W232/W261
# already established that artifact ``content_inline`` carries an
# ``INLINE_CONTENT_SOFT_LIMIT_BYTES`` (8 KiB) per-artifact advisory
# ceiling, but the WHOLE-PACKET serialised size was unbounded - a
# producer could legitimately fit hundreds of small inlined envelopes
# and ship a multi-megabyte ``ChangeEvidence`` on the wire. W280 adds
# the packet-level budget.
#
# SCOPE: this budget applies to the FULL canonical-JSON bytes of one
# ``ChangeEvidence``, measured inside ``with_content_hash`` BEFORE the
# content_hash is stamped. When the packet exceeds the budget, the
# deterministic truncation pipeline in ``_apply_size_budget`` fires
# (see ``_BUDGET_TRUNCATION_STEPS``), starting with
# ``artifacts[].content_inline`` -> ``None``. The truncation is
# enforced - it is not advisory - and ``redactions`` gains the
# ``"size_limit"`` reason whenever any step fires.
#
# Budget value: 256 KiB. Empirically the canonical JSON of a typical
# `roam pr-replay HEAD~2..HEAD --evidence` packet on a mid-sized repo
# sits between 10-40 KB; 256 KB gives 6-25x headroom for legitimate
# growth (more findings, more refs, larger context_refs) while keeping
# the wire footprint tractable for downstream tools that load packets
# into memory.
#
# Tunable as a module constant rather than a magic number so future
# operators can adjust without code spelunking; the doctor surfaces
# both ``packet_size_bytes`` and the constant in ``budget_bytes`` so
# adjustments are observable.
#
# See also: ``INLINE_CONTENT_SOFT_LIMIT_BYTES`` on ``EvidenceArtifact``
# (``src/roam/evidence/artifact.py``) for the per-artifact upstream
# pressure signal. The two limits operate at DIFFERENT scopes with
# DIFFERENT enforcement semantics:
#   * 8 KiB   - per artifact, advisory, no runtime enforcement
#   * 256 KiB - per packet, enforced, deterministic truncation
# Producers SHOULD respect the per-artifact ceiling to keep inline
# payloads small enough that the packet-level budget rarely trips;
# when it does trip, ``artifacts[].content_inline`` is the FIRST drop
# target (step 1 of 5) in the truncation pipeline.
PACKET_SIZE_BUDGET_BYTES: int = 256 * 1024  # 256 KiB = 262144 bytes


# Closed enumeration of packet-budget states. Frozen tuple so the doctor
# and any future consumer can route on a literal-string state without
# importing an enum.
#
# * ``within_budget`` - serialised size <= ``PACKET_SIZE_BUDGET_BYTES``
# * ``truncated`` - serialised size was > budget, truncation steps fired,
#                   resulting packet now fits the budget
# * ``oversized_after_truncation`` - serialised size still > budget even
#                                    after applying every truncation step.
#                                    Surfaced as WARN (not FAIL) so the
#                                    packet still renders; reviewer sees
#                                    the bloat explicitly.
PACKET_BUDGET_STATES: tuple[str, ...] = (
    "within_budget",
    "truncated",
    "oversized_after_truncation",
)


@dataclasses.dataclass(frozen=True)
class ChangeEvidence:
    """One evidence packet for one code-change scope.

    Field semantics map to the architecture memo lines 80-109. The
    packet is frozen so a producer can't mutate it after handing it
    to a consumer - the canonical way to update fields is
    ``dataclasses.replace(packet, field=new_value)``.

    Tuple-typed collections (``run_ids``, ``changed_subjects``,
    ``findings``, ...) are tuples (not lists) so the dataclass is
    hashable and the canonical-JSON serialisation order is stable.
    Callers pass tuples or use ``tuple(my_list)`` at construction.

    ``findings``, ``tests_run``, ``accepted_risks`` remain typed as
    tuples of ``Mapping[str, Any]`` for the Phase 1 / W174 contract -
    the architecture memo punts their rich types to a later phase and
    consumers hand-build dicts that the canonical-JSON serialiser
    normalises.

    ``policy_decisions`` was promoted to :class:`PolicyDecision` by
    W279 mirroring :class:`roam.evidence.approval.ApprovalRecord`. The
    field still ACCEPTS raw mappings on construction (so callers and
    tests that hand-build dicts continue to work) - the
    ``__post_init__`` normaliser pipes conforming rows through
    :meth:`PolicyDecision.from_dict` and leaves non-conforming rows
    (missing ``rule_id`` or ``decision``) untouched. Canonical-JSON
    serialisation invokes :meth:`PolicyDecision.to_dict` on each typed
    instance to flatten ``extra`` back into top-level keys, so the on-
    wire bytes are byte-identical to pre-W279 packets. See the W279
    note on :func:`_to_canonical_obj` below for the serialisation hook.

    W182 backward-compat decision (agentic-assurance crosswalk)
    -----------------------------------------------------------
    W182 introduced three optional ref lists - ``actor_refs``,
    ``authority_refs``, ``environment_refs`` - on top of the v0 schema.
    To preserve the content-hash contract with pre-W182 packets
    (downstream consumers already store and verify these hashes), the
    canonical JSON serialiser SKIPS these three fields when their value
    is an empty tuple. The decision is:

    * **Option (a) - OMIT empty refs from canonical JSON** (chosen).
      Pre-W182 packets and W182 packets WITHOUT refs produce IDENTICAL
      content hashes. ``schema_version`` stays ``"1.0.0"``.
    * Option (b) was: emit empty arrays for the new fields, bump
      ``schema_version`` to ``"1.1.0"``. Rejected because it breaks
      every stored v0 hash for zero functional gain (the field would
      always be ``[]`` for v0 consumers).

    Implication: when a producer populates ANY of the three ref lists,
    the resulting packet's hash differs from an otherwise-identical
    pre-W182 packet. That's correct behaviour - the packet IS
    different. Empty-list semantics: ``actor_refs == ()`` means "no
    actors attached"; a consumer that needs the distinction between
    "no actors attached" and "v0 packet, refs not yet a concept" reads
    the absence of the JSON key. This is consistent with how OpenVEX
    treats optional vulnerability statements.

    Field-order note: the three new fields are placed between
    ``artifacts`` and ``redactions``. They form a logical "assurance
    signature" group ordered to mirror the crosswalk memo's "identity
    + authority + evidence" framing - identity (actors) first,
    authority second, environment third. This ordering does not affect
    canonical JSON output (``sort_keys=True`` re-sorts alphabetically),
    but Python dataclass field order is part of the constructor
    contract.

    W210 backward-compat decision (per-claim + time + stale + versions)
    -------------------------------------------------------------------
    W210 adds six logical groups on top of W182:

    1. Per-claim confidence (``confidence_basis`` on each *finding row*
       - the findings collection is ``Mapping[str, Any]`` so this is a
       producer convention, not a dataclass field; vocabulary in
       ``_vocabulary.CLAIM_CONFIDENCES``).
    2. Time-aware evidence: ``context_read_at`` / ``edits_started_at``
       / ``edits_completed_at`` - change-scope timestamps distinct from
       the run-wide ``started_at`` / ``completed_at``.
    3. Stale-evidence detection: ``evidence_stale`` flag and
       ``stale_reasons`` tuple. The collector sets these when timestamps
       disagree (e.g. file mtime > preflight timestamp).
    4. Version + config linking: ``roam_version``, ``rules_config_hash``,
       ``constitution_hash``, ``control_map_hash`` - identify exactly
       which roam version + configuration produced this packet.
    5. Minimum-viable-assurance gate: :meth:`assurance_floor`.
    6. Report honesty banner: :meth:`evidence_completeness`.

    The same backward-compat discipline applies as W182: all field
    additions are omitted from canonical JSON when at their default
    value (``None`` / ``False`` / ``()``). See
    :data:`_W210_OMIT_WHEN_DEFAULT_FIELDS` for the per-field sentinel
    map. Pre-W210 packets and W210 packets that don't populate the new
    fields produce byte-identical canonical JSON, so stored content
    hashes stay valid. ``schema_version`` stays ``"1.0.0"``. The two new
    methods (``assurance_floor``, ``evidence_completeness``) are
    computed-only - they read existing fields and never modify the
    on-wire shape.
    """

    evidence_id: str
    schema_version: str = EVIDENCE_SCHEMA_VERSION
    repo_id: str | None = None
    git_range: str | None = None
    commit_sha: str | None = None
    diff_hash: str | None = None
    run_ids: tuple[str, ...] = ()
    agent_id: str | None = None
    human_actor: str | None = None
    mode: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    verdict: str | None = None
    risk_level: str | None = None
    context_refs: tuple[EvidenceArtifact, ...] = ()
    changed_subjects: tuple[EvidenceSubject, ...] = ()
    findings: tuple[Mapping[str, Any], ...] = ()
    # W279: ``policy_decisions`` rows are normalised through
    # :class:`PolicyDecision` (see ``policy.py``). Raw mappings are still
    # accepted for backward compat; ``__post_init__`` converts conforming
    # rows to typed instances. Non-conforming rows pass through as
    # ``Mapping`` so hand-crafted golden fixtures with legacy key shapes
    # (e.g. ``{"decision": ..., "rule": ...}``) round-trip byte-stably.
    policy_decisions: tuple[PolicyDecision | Mapping[str, Any], ...] = ()
    tests_required: tuple[str, ...] = ()
    tests_run: tuple[Mapping[str, Any], ...] = ()
    approvals: tuple[Mapping[str, Any], ...] = ()
    accepted_risks: tuple[Mapping[str, Any], ...] = ()
    artifacts: tuple[EvidenceArtifact, ...] = ()
    # W182 - agentic-assurance refs (identity / authority / environment).
    # Placed BEFORE redactions per the field-order note in this class's
    # docstring. Empty tuples are skipped from canonical JSON output so
    # pre-W182 hashes remain stable; see EVIDENCE_SCHEMA_VERSION comment
    # and _W182_OMIT_WHEN_EMPTY_FIELDS above.
    actor_refs: tuple[ActorRef, ...] = ()
    authority_refs: tuple[AuthorityRef, ...] = ()
    environment_refs: tuple[EnvironmentRef, ...] = ()
    redactions: tuple[str, ...] = ()
    # W210 (1/6) Per-claim confidence basis lives on each finding-row
    # dict (NOT as a dataclass field, because findings is Mapping-typed
    # per the Phase 1 contract). Vocabulary in
    # ``_vocabulary.CLAIM_CONFIDENCES``. Documented here so future
    # maintainers see the convention alongside the class definition.
    # W210 (2/6) Time-aware evidence - change-scope timestamps distinct
    # from the run-wide ``started_at`` / ``completed_at``. Default
    # ``None`` values are OMITTED from canonical JSON to preserve
    # pre-W210 content-hash stability.
    context_read_at: str | None = None
    edits_started_at: str | None = None
    edits_completed_at: str | None = None
    # W210 (3/6) Stale-evidence detection. The collector sets these when
    # the repo has changed after preflight / impact / tests ran. Defaults
    # ``False`` / ``()`` are OMITTED from canonical JSON.
    evidence_stale: bool = False
    stale_reasons: tuple[str, ...] = ()
    # W210 (4/6) Version + config linking. Identifies WHICH roam version
    # and WHICH on-disk config files produced this packet. All four
    # default to ``None`` and are OMITTED when ``None``.
    roam_version: str | None = None
    rules_config_hash: str | None = None
    constitution_hash: str | None = None
    control_map_hash: str | None = None
    content_hash: str | None = None
    signature_ref: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.evidence_id, str) or not self.evidence_id:
            raise ValueError("ChangeEvidence.evidence_id must be a non-empty string")
        if not isinstance(self.schema_version, str) or not self.schema_version:
            raise ValueError("ChangeEvidence.schema_version must be a non-empty string")
        # Coerce mutable inputs to tuples so consumers handing in lists
        # by accident still produce a hashable packet. Use object.__setattr__
        # because the dataclass is frozen.
        for field_name in (
            "run_ids",
            "context_refs",
            "changed_subjects",
            "findings",
            "policy_decisions",
            "tests_required",
            "tests_run",
            "approvals",
            "accepted_risks",
            "artifacts",
            # W182 agentic-assurance refs:
            "actor_refs",
            "authority_refs",
            "environment_refs",
            "redactions",
            # W210 stale-evidence tuple field:
            "stale_reasons",
        ):
            current = getattr(self, field_name)
            if not isinstance(current, tuple):
                object.__setattr__(self, field_name, tuple(current))

        # Validate redaction reasons (same closed enumeration as
        # EvidenceArtifact; reuses the import-level constant).
        from roam.evidence._vocabulary import REDACTION_REASONS

        for reason in self.redactions:
            if reason not in REDACTION_REASONS:
                raise ValueError(f"ChangeEvidence.redactions: unknown reason {reason!r}")

        # W279: normalise policy_decisions rows. Conforming dict rows
        # (carrying ``rule_id`` + ``decision``) flow through
        # ``PolicyDecision.from_dict`` so the typed contract holds
        # internally. Non-conforming rows (e.g. hand-crafted golden
        # fixtures missing ``rule_id`` or ``decision``) pass through
        # untouched so canonical-JSON serialisation produces byte-
        # identical output to the pre-W279 free-form dict shape.
        # Existing ``PolicyDecision`` instances pass through. The
        # dataclass is frozen, so we rebuild the tuple in place via
        # ``object.__setattr__``.
        #
        # W279b drift guard: when BOTH ``rule_id`` and ``decision`` are
        # present on the dict, the row is a "modern" producer shape and
        # MUST satisfy ``POLICY_DECISIONS`` closed-enum validation. A
        # ValueError from ``PolicyDecision.from_dict`` in that case
        # signals real producer drift (e.g. ``decision="approved"``
        # instead of ``"allow"``) and we re-raise rather than silently
        # preserving the bad row. Legacy partial shapes (missing
        # ``rule_id`` OR ``decision``) still pass through untouched.
        normalised_pd: list[PolicyDecision | Mapping[str, Any]] = []
        for row in self.policy_decisions:
            if isinstance(row, PolicyDecision):
                normalised_pd.append(row)
                continue
            if isinstance(row, Mapping):
                has_rule_id = bool(row.get("rule_id"))
                has_decision = bool(row.get("decision"))
                if has_rule_id and has_decision:
                    # Modern shape: enforce the closed-enum + typed
                    # contract. Any ValueError surfaces immediately.
                    normalised_pd.append(PolicyDecision.from_dict(row))
                else:
                    # Legacy / partial shape (no rule_id OR no decision):
                    # preserve byte-stability with pre-W279 fixtures.
                    normalised_pd.append(row)
                continue
            # Defensive: any non-Mapping non-PolicyDecision sneaking in
            # is preserved verbatim so the constructor stays forgiving
            # (canonical-JSON's str() fallback handles the wire side).
            normalised_pd.append(row)
        object.__setattr__(self, "policy_decisions", tuple(normalised_pd))

    # ------------------------------------------------------------------
    # W534 - Canonical JSON parsing (inverse of ``to_canonical_json``)
    # ------------------------------------------------------------------

    @classmethod
    def from_canonical_json(
        cls,
        text: str,
        *,
        strict: bool = False,
    ) -> ChangeEvidence:
        """Parse a canonical-JSON packet into a ``ChangeEvidence`` instance.

        Inverse of :meth:`to_canonical_json`. The round-trip contract is:

            packet == ChangeEvidence.from_canonical_json(packet.to_canonical_json())

        and (byte-stability):

            packet.to_canonical_json() == ChangeEvidence.from_canonical_json(
                packet.to_canonical_json()
            ).to_canonical_json()

        Both equalities hold for every fixture in
        ``tests/fixtures/evidence/``. The W182 / W210 omit-when-default rule
        is preserved: a fixture with no ``actor_refs`` key parses into a
        packet whose ``actor_refs`` is the empty tuple, and re-serialising
        omits the key again (so ``content_hash`` stays valid).

        Validation
        ----------
        Nested dataclasses (``EvidenceSubject``, ``EvidenceArtifact``,
        ``ActorRef``, ``AuthorityRef``, ``EnvironmentRef``,
        ``PolicyDecision`` when the row has both ``rule_id`` + ``decision``)
        validate their closed-enum fields at construction time. The
        ``redactions`` tuple on :class:`ChangeEvidence` is validated against
        :data:`REDACTION_REASONS` via the existing ``__post_init__`` path.

        ``strict`` mode
        ---------------
        * ``strict=False`` (default, conservative) - the parser tolerates
          unknown enum values on the nested dataclasses by emitting a
          :class:`UserWarning` and DROPPING the offending row. The packet
          still loads. Default-conservative because external producers
          (OSCAL consumers, third-party SARIF tools) may legitimately emit
          superset values, and a hard-fail by default would break the
          inverse-of-serialiser contract for any consumer reading a future
          producer's output. Dropping the row keeps the packet structurally
          valid while documenting the gap.
        * ``strict=True`` - any unknown enum value re-raises the underlying
          ``ValueError`` so producers can hard-fail on drift. Use in CI /
          validators / tests that want to lock the vocabulary.

        Free-form mapping rows (``findings``, ``tests_run``, ``approvals``,
        ``accepted_risks``) are passed through verbatim - Phase 1 types them
        as ``Mapping[str, Any]`` so there is nothing to validate beyond
        "it parses as a dict".

        Raises:
            ValueError: malformed JSON, missing/empty ``evidence_id``, OR
                any closed-enum violation when ``strict=True``.
        """
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"ChangeEvidence.from_canonical_json: malformed JSON ({exc.msg} at line {exc.lineno} col {exc.colno})"
            ) from exc
        if not isinstance(parsed, dict):
            raise ValueError(
                f"ChangeEvidence.from_canonical_json: expected a JSON object at top level, got {type(parsed).__name__}"
            )
        return _build_change_evidence(parsed, strict=strict)

    # ------------------------------------------------------------------
    # W561 - Pattern 1 variant D disclosure: drop-aware parsing
    # ------------------------------------------------------------------

    @classmethod
    def from_canonical_json_with_drops(
        cls,
        text: str,
        *,
        strict: bool = False,
    ) -> tuple[ChangeEvidence, list[str]]:
        """Parse a canonical-JSON packet and report dropped rows.

        Companion to :meth:`from_canonical_json`. Returns a 2-tuple of
        ``(packet, drops)`` where ``drops`` is the list of
        human-readable reasons for each row dropped under
        ``strict=False``. The list is always present and empty when no
        rows were dropped (so callers can branch on ``bool(drops)``).

        Under ``strict=True`` the parser raises on the first violation,
        and the returned tuple is unreachable in that path - the caller
        sees a ``ValueError`` instead. The list is therefore only
        meaningful in non-strict mode.

        This API exists to seal Pattern 1 variant D in
        ``CLAUDE.md`` ("Silent success on degraded resolution"). Callers
        that need to expose the drop count on a structured response
        envelope use this method; callers that only need the packet (and
        accept the legacy "warnings stream" disclosure path) keep using
        the original :meth:`from_canonical_json`. Both share the same
        underlying parser - no behavioural drift.

        Raises:
            ValueError: same conditions as :meth:`from_canonical_json`.
        """
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"ChangeEvidence.from_canonical_json: malformed JSON ({exc.msg} at line {exc.lineno} col {exc.colno})"
            ) from exc
        if not isinstance(parsed, dict):
            raise ValueError(
                f"ChangeEvidence.from_canonical_json: expected a JSON object at top level, got {type(parsed).__name__}"
            )
        drops: list[str] = []
        packet = _build_change_evidence(parsed, strict=strict, drops=drops)
        return packet, drops

    # ------------------------------------------------------------------
    # Canonical JSON serialisation
    # ------------------------------------------------------------------

    def to_canonical_json(self) -> str:
        """Return deterministic JSON for this packet.

        Properties:

        * sort_keys=True at every dict level, so dict iteration order
          can't leak into the bytes.
        * separators=(",", ":") removes insignificant whitespace.
        * Tuples serialise as JSON arrays (preserves declared order).
        * Nested dataclasses (``EvidenceSubject``, ``EvidenceLink``,
          ``EvidenceArtifact``, W182 ``ActorRef`` / ``AuthorityRef`` /
          ``EnvironmentRef``) flatten via ``dataclasses.asdict``,
          which recursively converts to plain dict / list / tuple.
        * ``None`` values are kept (not stripped) so round-trip is
          shape-stable; consumers that want compact output can post-
          process.
        * W182 omission rule: the three agentic-assurance ref fields
          (``actor_refs`` / ``authority_refs`` / ``environment_refs``)
          are SKIPPED when their value is an empty tuple. This
          preserves byte-equivalence with pre-W182 canonical JSON for
          packets that don't populate refs, which in turn keeps the
          content_hash backward-compatible. See class docstring "W182
          backward-compat decision" for the full rationale.
        * W210 omission rule: the W210 field-groups (time-aware
          timestamps, stale flag + reasons, version-link hashes) are
          SKIPPED when their canonicalised value equals the per-field
          default sentinel (``None`` for the eight scalar fields,
          ``False`` for ``evidence_stale``, ``[]`` for
          ``stale_reasons`` post-canonicalisation). Same rationale:
          a pre-W210 packet and a W210-with-defaults packet must
          produce byte-identical canonical JSON.
        """
        payload = _to_canonical_obj(self)
        # W182 backward-compat: drop the three new ref fields when
        # they are empty so pre-W182 packets and W182-without-refs
        # packets produce byte-identical canonical JSON.
        if isinstance(payload, dict):
            for k in _W182_OMIT_WHEN_EMPTY_FIELDS:
                if payload.get(k) == []:
                    payload.pop(k, None)
            # W210 backward-compat: drop each W210 field when its
            # canonicalised value equals the per-field default sentinel
            # in ``_W210_OMIT_WHEN_DEFAULT_FIELDS``.
            for k, default in _W210_OMIT_WHEN_DEFAULT_FIELDS.items():
                if k in payload and payload[k] == default:
                    payload.pop(k, None)
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    # ------------------------------------------------------------------
    # Content hash
    # ------------------------------------------------------------------

    def compute_content_hash(self) -> str:
        """Return sha256(canonical_json) with ``content_hash`` cleared.

        Why clear it: the hash is part of the packet, so including it
        in the hash input would be circular. We compute the hash on
        the packet *without* this field and stamp the result back.
        """
        stripped = dataclasses.replace(self, content_hash=None)
        canonical = stripped.to_canonical_json()
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def with_content_hash(self) -> ChangeEvidence:
        """Return a copy of self with ``content_hash`` populated.

        W280 packet-size discipline: BEFORE computing the canonical-JSON
        bytes that feed the sha256 digest, the packet is run through
        :meth:`_apply_size_budget` so that the resulting ``content_hash``
        is the hash of the POST-truncation packet. Consumers verifying
        the on-wire hash and consumers serialising the same in-memory
        packet via :meth:`to_canonical_json` therefore see identical
        bytes regardless of whether truncation fired.

        Within-budget packets (the common case) bypass the truncation
        path entirely - ``_apply_size_budget`` returns ``self`` and no
        ``dataclasses.replace`` is constructed for the budget step.
        """
        budgeted = self._apply_size_budget()
        digest = budgeted.compute_content_hash()
        return dataclasses.replace(budgeted, content_hash=digest)

    # ------------------------------------------------------------------
    # W280 packet-size budget
    # ------------------------------------------------------------------

    def _apply_size_budget(self) -> ChangeEvidence:
        """Return a packet whose canonical JSON fits the size budget.

        Pure: returns ``self`` unchanged when the packet is already
        within budget; otherwise returns a NEW ``ChangeEvidence`` with
        truncation applied in a fixed, deterministic order. The
        ``redactions`` tuple gets ``"size_limit"`` appended (dedup-safe)
        whenever any truncation step fires.

        Truncation order (frozen, see ``_BUDGET_TRUNCATION_STEPS``):

        1. ``artifacts[].content_inline`` -> ``None`` (biggest known
           inline payload kind; raw_envelope artifacts can carry 10s of
           KB each).
        2. ``context_refs[].content_inline`` -> ``None`` (W246 fallback
           path for missing content_hash; same shape as artifacts).
        3. ``policy_decisions[].extra`` cleared (the ``extra`` mapping is
           free-form and can balloon when producers emit verbose
           reasons or evidence-ref payloads).
        4. ``findings[].evidence`` cleared if such a key is present on
           the row (Phase-1 finding shape is ``Mapping[str, Any]``; the
           ``evidence`` key is a convention some producers use to
           attach supplementary detail).
        5. ``actor_refs[].extra`` cleared (free-form structured detail;
           per the W182 docstring this is "kept tiny" but a
           non-conforming producer might inflate it).

        ``redactions`` itself is NEVER dropped (it's the channel that
        DOCUMENTS the truncation; dropping it would defeat the purpose).
        After step 5, if the packet is STILL over budget,
        :attr:`packet_budget_state_for` (via the helper) returns
        ``"oversized_after_truncation"`` and the ``"size_limit"`` reason
        is stamped anyway. Reviewer sees the bloat explicitly through
        :func:`roam.commands.cmd_evidence_doctor.evidence_doctor`.

        This method participates in the canonical-JSON / content-hash
        contract through :meth:`with_content_hash`: the hash is computed
        on the OUTPUT of this method, not on the input.
        """
        # Compute the size of the current packet's canonical JSON; if
        # within budget, return self unchanged (no allocation).
        current_bytes = len(self.to_canonical_json().encode("utf-8"))
        if current_bytes <= PACKET_SIZE_BUDGET_BYTES:
            return self

        # Over budget: run truncation steps in declared order, stopping
        # as soon as the canonical-JSON size fits the budget. Each step
        # returns (next_packet, fired_bool); fired_bool is True iff the
        # step actually changed the packet shape (so the
        # ``size_limit`` redaction tag stamp logic stays accurate).
        working = self
        any_step_fired = False
        for step in _BUDGET_TRUNCATION_STEPS:
            next_packet, fired = step(working)
            if fired:
                any_step_fired = True
                working = next_packet
                if len(working.to_canonical_json().encode("utf-8")) <= PACKET_SIZE_BUDGET_BYTES:
                    break

        # Always stamp ``size_limit`` when ANY truncation step fired (or
        # when the packet was over budget at entry). Dedup-safe: if the
        # reason is already present (e.g. a producer stamped it on a
        # prior pass), don't append a duplicate.
        if any_step_fired or len(working.to_canonical_json().encode("utf-8")) > PACKET_SIZE_BUDGET_BYTES:
            if "size_limit" not in working.redactions:
                new_redactions = tuple(list(working.redactions) + ["size_limit"])
                working = dataclasses.replace(working, redactions=new_redactions)

        return working

    # ------------------------------------------------------------------
    # W210 (5/6) Minimum-viable-assurance gate
    # ------------------------------------------------------------------

    def assurance_floor(self) -> dict[str, Any]:
        """Return the minimum-viable-assurance (MVA) gate status.

        Names the floor below which an evidence packet is not safe to
        publish as governance evidence. Pass requires ALL six of:

        * **actor**            - any ``actor_refs`` entry OR ``agent_id``
                                 set.
        * **authority**        - any ``authority_refs`` entry.
        * **changed_subjects** - non-empty.
        * **findings**         - non-empty.
        * **verification**     - ``tests_run`` non-empty OR a
                                 ``redactions`` entry / ``accepted_risks``
                                 entry that explicitly acknowledges the
                                 gap ("limitations explained").
        * **policy_state**     - any ``policy_decisions`` entry OR any
                                 ``authority_refs`` entry.

        Returns:
            ``{"passes": bool, "missing": tuple[str, ...],
              "stale": bool, "stale_reasons": tuple[str, ...]}``
            - the ``missing`` tuple is in declared-check order so
            consumers can render it as a checklist; ``stale`` mirrors
            :attr:`evidence_stale` and ``stale_reasons`` mirrors
            :attr:`stale_reasons` so a consumer can decide whether to
            attest at the MVA level reported by ``passes`` (W1254).

        W1254 design choice (additive, NOT integrated). Staleness is a
        distinct quality axis from MVA-floor coverage: a packet can be
        MVA-complete AND stale (six axes covered, but the context-read
        post-dates edits), or MVA-incomplete AND fresh. Conflating the
        two would erode actionable signal — a stale-but-complete packet
        should still report ``passes=True`` so the verifier knows the
        floor was met, while the new ``stale`` field warns that
        downstream consumers SHOULD treat the verdict with caution.
        :func:`roam.attest.vsa._verified_levels` and
        :func:`roam.attest.vsa._verification_result` are the canonical
        downstream readers. ``_verified_levels`` consumes only
        ``passes`` and ``missing``; ``_verification_result``
        additionally consumes ``stale`` to downgrade stale-but-MVA-
        complete packets to ``FAILED`` per W1261. The additive shape
        keeps both readers green and lets the attestation boundary
        refuse silent-success on degraded resolution (Pattern-2
        discipline). ``stale_reasons`` is exposed for non-VSA
        consumers (e.g. report banners) that want to render the cause.

        This is a computed read; it does NOT modify the packet and does
        NOT participate in the canonical-JSON / content-hash contract.
        Producers can call it after construction to decide whether to
        emit or to log a "below-floor" warning.
        """
        missing: list[str] = []

        actor_ok = bool(self.actor_refs) or bool(self.agent_id)
        if not actor_ok:
            missing.append("actor")

        authority_ok = bool(self.authority_refs)
        if not authority_ok:
            missing.append("authority")

        changes_ok = bool(self.changed_subjects)
        if not changes_ok:
            missing.append("changed_subjects")

        findings_ok = bool(self.findings)
        if not findings_ok:
            missing.append("findings")

        # "tests OR limitations explained": limitations-explained is
        # signalled by a non-empty ``redactions`` tuple (the producer
        # named the gap) OR by a non-empty ``accepted_risks`` collection
        # (the gap was acknowledged).
        verification_ok = bool(self.tests_run) or bool(self.redactions) or bool(self.accepted_risks)
        if not verification_ok:
            missing.append("verification")

        # Policy state: any policy_decisions OR any authority_refs.
        # ``authority_refs`` already gates "authority present" above; this
        # check is broader (any structured policy claim counts).
        policy_ok = bool(self.policy_decisions) or bool(self.authority_refs)
        if not policy_ok:
            missing.append("policy_state")

        return {
            "passes": not missing,
            "missing": tuple(missing),
            # W1254 - additive staleness signal. ``stale`` is a
            # SEPARATE axis from ``passes``; consumers that care MUST
            # gate on both.
            "stale": bool(self.evidence_stale),
            "stale_reasons": tuple(self.stale_reasons),
        }

    # ------------------------------------------------------------------
    # W210 (6/6) Report honesty banner
    # ------------------------------------------------------------------

    def evidence_completeness(self) -> dict[str, Any]:
        """Return per-question completeness for the 8 evidence questions.

        The eight questions (per the 2026-05-14 directive item 6) frame
        the minimum content an honest governance report should answer:

        * Q1 actor       - WHO made the change?
        * Q2 authority   - WHO authorised the change?
        * Q3 context     - WHAT context did the actor read?
        * Q4 changes     - WHAT changed?
        * Q5 risk        - WHAT risk did the change introduce?
        * Q6 policy      - WHAT policy decisions were made?
        * Q7 verify      - HOW was the change verified?
        * Q8 accept      - WHO accepted any residual risk?

        Each Q1..Q8 value is one of ``"complete"`` / ``"partial"`` /
        ``"missing"`` / ``"not_applicable"``. The returned dict also
        carries totals (``complete`` / ``partial`` / ``missing`` /
        ``not_applicable``) so consumers can render a one-line banner
        ("4 complete, 2 partial, 2 missing") without re-counting.

        ``partial`` is reserved for cases where a field carries weak
        signal but the corroborating structured field is absent - e.g.
        Q1 with only ``actor_id`` set but no ``actor_refs``. The single
        ``not_applicable`` path today is Q5 when ``verdict`` is "SAFE"
        / "PASS" AND there are no findings (no risk to claim).

        W1254 staleness penalty (integrated). When
        :attr:`evidence_stale` is ``True``, every ``"complete"`` Q is
        demoted to ``"partial"`` and a ``stale`` flag plus
        ``stale_reasons`` tuple are included in the returned dict so a
        consumer can detect the demotion and surface a "stale evidence"
        warning without re-reading the underlying field. The demote-
        not-discard choice (``complete`` -> ``partial`` rather than
        ``complete`` -> ``missing``) is principled: the structured data
        IS present, it is just no-longer-trustworthy as a "complete"
        signal. ``partial`` accurately conveys that erosion of trust
        while preserving the invariant ``complete + partial + missing
        + not_applicable == 8`` that downstream consumers
        (``banner.py``, ``cmd_evidence_doctor``) already rely on. A
        stale-but-otherwise-fully-complete packet ends up classified as
        PARTIAL by ``banner.classify_evidence_coverage`` (it would have
        been STRONG when fresh) — the exact penalty the W1234 producer
        wire-up was meant to surface.

        Returns:
            ``{"Q1": "...", ..., "Q8": "...",
              "complete": N, "partial": N, "missing": N,
              "not_applicable": N,
              "stale": bool, "stale_reasons": tuple[str, ...]}``

        This is a computed read; it does NOT modify the packet and does
        NOT participate in the canonical-JSON / content-hash contract.
        """
        result: dict[str, Any] = {}

        # Q1 actor: complete if actor_refs; partial if agent_id or
        # human_actor only; missing otherwise.
        if self.actor_refs:
            result["Q1"] = "complete"
        elif self.agent_id or self.human_actor:
            result["Q1"] = "partial"
        else:
            result["Q1"] = "missing"

        # Q2 authority: complete if authority_refs; partial if mode only;
        # missing otherwise.
        if self.authority_refs:
            result["Q2"] = "complete"
        elif self.mode:
            result["Q2"] = "partial"
        else:
            result["Q2"] = "missing"

        # Q3 context: complete if context_refs; missing otherwise.
        if self.context_refs:
            result["Q3"] = "complete"
        else:
            result["Q3"] = "missing"

        # Q4 changes: complete if changed_subjects; missing otherwise.
        if self.changed_subjects:
            result["Q4"] = "complete"
        else:
            result["Q4"] = "missing"

        # Q5 risk: complete if risk_level; not_applicable if verdict is
        # SAFE/PASS with no findings; missing otherwise.
        if self.risk_level:
            result["Q5"] = "complete"
        elif self.verdict in ("SAFE", "PASS", "safe", "pass") and not self.findings:
            result["Q5"] = "not_applicable"
        else:
            result["Q5"] = "missing"

        # Q6 policy: complete if policy_decisions; partial if
        # authority_refs only (authority gates carry an implicit policy
        # decision but it's not structured); missing otherwise.
        if self.policy_decisions:
            result["Q6"] = "complete"
        elif self.authority_refs:
            result["Q6"] = "partial"
        else:
            result["Q6"] = "missing"

        # Q7 verify: complete if tests_run OR a verification-shaped
        # artifact exists; partial if tests_required OR only report /
        # manifest / other artifacts exist; missing otherwise.
        # This prevents a generated report artifact from making a replay
        # look fully verified when no tests or attestations ran.
        if self.tests_run or _has_verification_artifact(self.artifacts):
            result["Q7"] = "complete"
        elif self.tests_required or self.artifacts:
            result["Q7"] = "partial"
        else:
            result["Q7"] = "missing"

        # Q8 accept: complete if approvals OR accepted_risks; partial if
        # redactions explicitly named a gap (limitation acknowledged);
        # missing otherwise. The W261 ``producer_not_available`` reason
        # is the honest vocabulary for the "no harvester wired into this
        # pipeline" case (e.g. pr-replay's lack of an approvals
        # harvester); it lifts Q8 from ``missing`` to ``partial`` so the
        # banner stays accurate without falsely implying acceptance was
        # checked. Any other REDACTION_REASONS entry also lifts Q8 to
        # ``partial`` because the producer at least declared SOME masking
        # context for the change-scope.
        if self.approvals or self.accepted_risks:
            result["Q8"] = "complete"
        elif self.redactions:
            result["Q8"] = "partial"
        else:
            result["Q8"] = "missing"

        # W1254 - staleness demotion. When the packet is stale, every
        # ``complete`` Q is demoted to ``partial``. The structured data
        # is still present (so ``missing`` would be a lie) but it is
        # no-longer-trustworthy as a "complete" signal (so leaving it
        # as ``complete`` would also be a lie). ``partial`` is the
        # honest middle ground; the eight-Q invariant
        # (``complete + partial + missing + not_applicable == 8``)
        # is preserved. ``not_applicable`` is NOT demoted — a question
        # that does not apply remains inapplicable regardless of how
        # stale the rest of the packet is.
        if self.evidence_stale:
            for q_key in ("Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "Q8"):
                if result[q_key] == "complete":
                    result[q_key] = "partial"

        # Totals - count over only the Q1..Q8 entries (not the totals
        # themselves).
        q_values = [result[f"Q{i}"] for i in range(1, 9)]
        result["complete"] = sum(1 for v in q_values if v == "complete")
        result["partial"] = sum(1 for v in q_values if v == "partial")
        result["missing"] = sum(1 for v in q_values if v == "missing")
        result["not_applicable"] = sum(1 for v in q_values if v == "not_applicable")
        # W1254 - expose the staleness signal alongside the table so a
        # consumer that reads ONLY the dict (no access to the packet)
        # can detect the demotion. ``stale_reasons`` mirrors the
        # field so the consumer can render the WHY without re-reading
        # ``self``.
        result["stale"] = bool(self.evidence_stale)
        result["stale_reasons"] = tuple(self.stale_reasons)
        return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def resolve_roam_version() -> str:
    """Return the installed ``roam-code`` package version, or ``"unknown"``.

    W287 directive. Producers that build a :class:`ChangeEvidence`
    packet should stamp :attr:`ChangeEvidence.roam_version` from this
    helper rather than hard-coding a version string. The helper defers
    the ``from roam import __version__`` import to call time so the
    module-import graph stays clean (``roam.evidence.change_evidence``
    is loaded during package init by some collector paths; importing
    ``roam`` from the module scope here would re-enter the package init
    flow).

    Behaviour:

    * Returns ``roam.__version__`` when ``importlib.metadata`` resolves
      the package (the normal install path; ``roam-code`` is on PyPI).
    * Returns ``"unknown"`` when the package version import cannot
      resolve. The fallback string is a sentinel a downstream consumer
      can detect (versus an empty string, which could be ambiguous with
      "field unset"); deliberately uses NO punctuation so it stays valid
      in places that constrain string content (e.g. SARIF tool-version
      fields).

    Backward-compat: this helper is NOT wired into the dataclass field
    default. The default stays ``None`` (omit-when-default rule), so
    every existing golden hash and every existing test that builds a
    bare ``ChangeEvidence`` continues to pass byte-stably. Producers
    that want the version stamp pass it explicitly:

        packet = ChangeEvidence(
            evidence_id="...",
            roam_version=resolve_roam_version(),
            ...
        )
    """
    try:
        # Deferred import: avoid re-entering ``roam`` package init when
        # this module is imported during early evidence-collector setup.
        from roam import __version__ as _rv

        # ``roam.__version__`` already falls back to ``"dev"`` when
        # ``importlib.metadata`` cannot resolve the package; pass that
        # through verbatim - both ``"dev"`` and a real semver string
        # are valid producer stamps.
        if isinstance(_rv, str) and _rv:
            return _rv
        return "unknown"
    except ImportError:
        return "unknown"


# ---------------------------------------------------------------------------
# W534 - Canonical JSON parsing (inverse of ``to_canonical_json``)
# ---------------------------------------------------------------------------


# Scalar fields that lift verbatim from the parsed dict into the
# ``ChangeEvidence`` constructor. Mapping-typed and tuple-typed fields are
# handled with dedicated logic below (they require nested-dataclass
# reconstruction or tuple coercion). Boolean ``evidence_stale`` is included
# here because it can be passed straight through; the constructor's
# coercion logic + omit-when-default rule preserves byte-stability.
_FROM_CANONICAL_SCALAR_FIELDS: tuple[str, ...] = (
    "evidence_id",
    "schema_version",
    "repo_id",
    "git_range",
    "commit_sha",
    "diff_hash",
    "agent_id",
    "human_actor",
    "mode",
    "started_at",
    "completed_at",
    "verdict",
    "risk_level",
    "content_hash",
    "signature_ref",
    # W210 time-aware + version-link scalars:
    "context_read_at",
    "edits_started_at",
    "edits_completed_at",
    "roam_version",
    "rules_config_hash",
    "constitution_hash",
    "control_map_hash",
    # W210 boolean (omitted when False):
    "evidence_stale",
)

# Tuple-of-string fields. Parsed JSON arrays coerce to tuples; the
# constructor's existing coercion loop accepts either.
_FROM_CANONICAL_TUPLE_OF_STR_FIELDS: tuple[str, ...] = (
    "run_ids",
    "tests_required",
    "redactions",
    "stale_reasons",
)

# Tuple-of-Mapping fields (Phase 1 free-form rows). Parsed as-is so a
# legacy fixture row like ``{"decision": "allow", "rule": "..."}`` (missing
# ``rule_id``) flows through unchanged; the ``ChangeEvidence`` constructor
# decides whether to normalise via ``PolicyDecision.from_dict``.
_FROM_CANONICAL_TUPLE_OF_MAPPING_FIELDS: tuple[str, ...] = (
    "findings",
    "policy_decisions",
    "tests_run",
    "approvals",
    "accepted_risks",
)


def _warn_or_raise(
    msg: str,
    *,
    strict: bool,
    exc: BaseException | None = None,
    drops: list[str] | None = None,
) -> None:
    """Strict-mode helper: raise ``ValueError`` or emit a ``UserWarning``.

    W561 Pattern 1 variant D fix: when ``drops`` is supplied AND we're in
    non-strict mode, also append the message to ``drops`` so the caller
    can surface a structured drop count on the response envelope (vs
    relying on warnings.catch_warnings to capture the message). The
    UserWarning is still emitted so existing callers that consume the
    warning stream keep working byte-for-byte.
    """
    if strict:
        if exc is not None:
            raise ValueError(msg) from exc
        raise ValueError(msg)
    import warnings as _warnings

    _warnings.warn(msg, UserWarning, stacklevel=3)
    if drops is not None:
        drops.append(msg)


def _build_subject(
    d: Mapping[str, Any],
    *,
    strict: bool,
    drops: list[str] | None = None,
) -> Any:
    """Build an ``EvidenceSubject`` from its canonical dict form.

    Returns ``None`` when the row is unusable (caller filters None rows).
    Non-strict mode warns and drops; strict mode raises. The optional
    ``drops`` list (W561) receives the human-readable reason for each
    dropped row so the caller can surface a structured drop count.
    """
    from roam.evidence.subject import EvidenceSubject

    try:
        return EvidenceSubject(
            kind=d["kind"],
            qualified_name=d["qualified_name"],
            repo_id=d.get("repo_id"),
            extra=d.get("extra") or {},
        )
    except (KeyError, ValueError) as exc:
        _warn_or_raise(
            f"ChangeEvidence.from_canonical_json: dropped changed_subject row {dict(d)!r}: {exc}",
            strict=strict,
            exc=exc,
            drops=drops,
        )
        return None


def _build_artifact(
    d: Mapping[str, Any],
    *,
    strict: bool,
    drops: list[str] | None = None,
) -> Any:
    """Build an ``EvidenceArtifact`` from its canonical dict form.

    Only forwards non-``None`` ``path`` / ``content_hash`` / ``content_inline``
    so the constructor's mutual-exclusion invariant
    (``path`` xor ``content_inline``) is respected.
    """
    from roam.evidence.artifact import EvidenceArtifact

    try:
        kwargs: dict[str, Any] = {
            "artifact_id": d["artifact_id"],
            "kind": d["kind"],
        }
        for k in ("path", "content_hash", "content_inline"):
            v = d.get(k)
            if v is not None:
                kwargs[k] = v
        red = d.get("redactions")
        if red:
            kwargs["redactions"] = tuple(red)
        extra = d.get("extra")
        if extra:
            kwargs["extra"] = extra
        return EvidenceArtifact(**kwargs)
    except (KeyError, ValueError) as exc:
        _warn_or_raise(
            f"ChangeEvidence.from_canonical_json: dropped artifact row {dict(d)!r}: {exc}",
            strict=strict,
            exc=exc,
            drops=drops,
        )
        return None


def _build_actor(
    d: Mapping[str, Any],
    *,
    strict: bool,
    drops: list[str] | None = None,
) -> Any:
    from roam.evidence.refs import ActorRef

    try:
        return ActorRef(
            actor_kind=d["actor_kind"],
            actor_id=d["actor_id"],
            display_name=d.get("display_name"),
            trust_tier=d.get("trust_tier", "unknown"),
            extra=d.get("extra") or {},
        )
    except (KeyError, ValueError) as exc:
        _warn_or_raise(
            f"ChangeEvidence.from_canonical_json: dropped actor_ref row {dict(d)!r}: {exc}",
            strict=strict,
            exc=exc,
            drops=drops,
        )
        return None


def _build_authority(
    d: Mapping[str, Any],
    *,
    strict: bool,
    drops: list[str] | None = None,
) -> Any:
    from roam.evidence.refs import AuthorityRef

    try:
        return AuthorityRef(
            authority_kind=d["authority_kind"],
            authority_id=d["authority_id"],
            granted_by=d.get("granted_by"),
            source=d.get("source", "inferred_fallback"),
            extra=d.get("extra") or {},
        )
    except (KeyError, ValueError) as exc:
        _warn_or_raise(
            f"ChangeEvidence.from_canonical_json: dropped authority_ref row {dict(d)!r}: {exc}",
            strict=strict,
            exc=exc,
            drops=drops,
        )
        return None


def _build_environment(
    d: Mapping[str, Any],
    *,
    strict: bool,
    drops: list[str] | None = None,
) -> Any:
    from roam.evidence.refs import EnvironmentRef

    try:
        return EnvironmentRef(
            env_kind=d["env_kind"],
            env_id=d["env_id"],
            extra=d.get("extra") or {},
        )
    except (KeyError, ValueError) as exc:
        _warn_or_raise(
            f"ChangeEvidence.from_canonical_json: dropped environment_ref row {dict(d)!r}: {exc}",
            strict=strict,
            exc=exc,
            drops=drops,
        )
        return None


def _build_change_evidence(
    parsed: Mapping[str, Any],
    *,
    strict: bool,
    drops: list[str] | None = None,
) -> ChangeEvidence:
    """Construct a ``ChangeEvidence`` from a parsed JSON dict.

    Drives the per-field reconstruction declared in
    ``_FROM_CANONICAL_*`` tables above. Filters ``None`` rows yielded by
    the nested-dataclass builders when ``strict=False`` (so a single bad
    row doesn't poison the whole packet).

    The optional ``drops`` list (W561) is forwarded to every nested
    builder; on return the caller can read ``len(drops)`` to learn how
    many rows were silently dropped in non-strict mode.
    """
    kwargs: dict[str, Any] = {}

    # Scalar / boolean fields (verbatim copy).
    for k in _FROM_CANONICAL_SCALAR_FIELDS:
        if k in parsed:
            kwargs[k] = parsed[k]

    # Tuple-of-string fields. The constructor coerces lists -> tuples but
    # we coerce here too so the test_evidence_schema_migration path stays
    # parallel to ``_load_packet``.
    for k in _FROM_CANONICAL_TUPLE_OF_STR_FIELDS:
        if k in parsed:
            value = parsed[k]
            if not isinstance(value, list):
                if strict:
                    raise ValueError(
                        f"ChangeEvidence.from_canonical_json: {k!r} must be a JSON array, got {type(value).__name__}"
                    )
                continue
            kwargs[k] = tuple(value)

    # Tuple-of-Mapping fields (preserved verbatim for Phase 1).
    for k in _FROM_CANONICAL_TUPLE_OF_MAPPING_FIELDS:
        if k in parsed:
            value = parsed[k]
            if not isinstance(value, list):
                if strict:
                    raise ValueError(
                        f"ChangeEvidence.from_canonical_json: {k!r} must be a JSON array, got {type(value).__name__}"
                    )
                continue
            kwargs[k] = tuple(value)

    # Nested-dataclass tuple fields.
    if "context_refs" in parsed and isinstance(parsed["context_refs"], list):
        rows = [
            _build_artifact(r, strict=strict, drops=drops) for r in parsed["context_refs"] if isinstance(r, Mapping)
        ]
        kwargs["context_refs"] = tuple(r for r in rows if r is not None)
    if "artifacts" in parsed and isinstance(parsed["artifacts"], list):
        rows = [_build_artifact(r, strict=strict, drops=drops) for r in parsed["artifacts"] if isinstance(r, Mapping)]
        kwargs["artifacts"] = tuple(r for r in rows if r is not None)
    if "changed_subjects" in parsed and isinstance(parsed["changed_subjects"], list):
        rows = [
            _build_subject(r, strict=strict, drops=drops) for r in parsed["changed_subjects"] if isinstance(r, Mapping)
        ]
        kwargs["changed_subjects"] = tuple(r for r in rows if r is not None)
    if "actor_refs" in parsed and isinstance(parsed["actor_refs"], list):
        rows = [_build_actor(r, strict=strict, drops=drops) for r in parsed["actor_refs"] if isinstance(r, Mapping)]
        kwargs["actor_refs"] = tuple(r for r in rows if r is not None)
    if "authority_refs" in parsed and isinstance(parsed["authority_refs"], list):
        rows = [
            _build_authority(r, strict=strict, drops=drops) for r in parsed["authority_refs"] if isinstance(r, Mapping)
        ]
        kwargs["authority_refs"] = tuple(r for r in rows if r is not None)
    if "environment_refs" in parsed and isinstance(parsed["environment_refs"], list):
        rows = [
            _build_environment(r, strict=strict, drops=drops)
            for r in parsed["environment_refs"]
            if isinstance(r, Mapping)
        ]
        kwargs["environment_refs"] = tuple(r for r in rows if r is not None)

    # Construct the packet. The dataclass's ``__post_init__`` runs its
    # own validation (evidence_id non-empty, redaction reasons, policy-
    # decision normalisation). When ``strict=True`` we let those raise
    # verbatim; when ``strict=False`` we still let them raise because
    # ``ChangeEvidence`` itself does not have a "drop and continue"
    # surface - if redactions or evidence_id are malformed, the packet
    # is structurally unusable.
    return ChangeEvidence(**kwargs)


def _to_canonical_obj(value: Any) -> Any:
    """Recursively convert ``value`` into JSON-canonical primitives.

    Rules:

    * :class:`PolicyDecision`-> dict via :meth:`PolicyDecision.to_dict`
                                (W279: flattens ``extra`` back to top-
                                level keys so byte-stability with
                                pre-W279 free-form dict rows is
                                preserved; ``dataclasses.asdict`` would
                                emit all six fields including ``None``
                                scalars and the empty ``extra`` dict,
                                breaking every stored content_hash)
    * dataclass instance     -> walk fields manually, recursing per
                                field (NOT ``dataclasses.asdict`` -
                                asdict short-circuits the W279
                                ``PolicyDecision`` hook above by
                                flattening nested dataclasses to dicts
                                eagerly, before this helper sees them)
    * Mapping (dict-like)    -> dict with canonicalised values
    * tuple / list           -> list (JSON has no tuple type)
    * primitives             -> identity
    * everything else        -> ``str(value)`` (defensive; should not
                                fire in normal use - log via test)
    """
    # W279: short-circuit ``PolicyDecision`` BEFORE the generic
    # ``is_dataclass`` branch. ``PolicyDecision`` is a dataclass too,
    # but the omit-when-default wire shape lives in its ``to_dict``
    # method, not in the dataclasses.asdict default. Must come before
    # the manual-walk dataclass branch below for the same reason.
    if isinstance(value, PolicyDecision):
        return _to_canonical_obj(value.to_dict())
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        # Walk one level at a time so the recursive call sees each
        # nested dataclass instance and can dispatch on it (e.g. hit
        # the PolicyDecision branch). Using ``dataclasses.asdict`` here
        # would eagerly flatten nested dataclasses BEFORE recursion,
        # bypassing the W279 hook above.
        return {f.name: _to_canonical_obj(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Mapping):
        return {str(k): _to_canonical_obj(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_canonical_obj(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    # Defensive fallback. Should not happen with the dataclasses we
    # define above; here so an unexpected type doesn't break the
    # deterministic-serialisation contract.
    return str(value)


def stale_accepted_risks(
    packet: ChangeEvidence,
    *,
    now_iso: str | None = None,
) -> tuple[Mapping[str, Any], ...]:
    """Return ``accepted_risks`` entries whose ``expiry`` has passed.

    W211 directive. Each entry in :attr:`ChangeEvidence.accepted_risks`
    is a ``Mapping`` (the schema-v0 contract for the field is
    ``tuple[Mapping[str, Any], ...]`` - rich types are punted to a
    later phase). This helper walks the tuple and returns the subset
    whose ``"expiry"`` key parses as an ISO-8601 timestamp in the
    past.

    Rules:

    * Entries without an ``"expiry"`` key (or with ``None``) never go
      stale - they are not returned.
    * Entries whose ``"expiry"`` value is unparseable as ISO-8601 are
      treated as stale (failing open would silently hide bad data;
      better to surface it so the producer fixes the field).
    * ``now_iso`` is overridable for deterministic tests; when omitted,
      uses the current UTC time.

    Returns a tuple (not a list) so callers can use the result in
    further hashable contexts without coercion.
    """
    if now_iso is None:
        now_dt = _dt.datetime.now(tz=_dt.timezone.utc)
    else:
        now_dt = _parse_iso(now_iso)

    stale: list[Mapping[str, Any]] = []
    for entry in packet.accepted_risks:
        if not isinstance(entry, Mapping):
            # Defensive: the field is typed as Mapping but a producer
            # might hand-build something stranger. Treat non-mappings
            # as not-stale (cannot interpret an expiry).
            continue
        expiry = entry.get("expiry")
        if expiry is None:
            continue
        if not isinstance(expiry, str) or not expiry:
            # Truthy-but-malformed: flag as stale so the producer
            # notices.
            stale.append(entry)
            continue
        try:
            expiry_dt = _parse_iso(expiry)
        except ValueError:
            stale.append(entry)
            continue
        if now_dt > expiry_dt:
            stale.append(entry)
    return tuple(stale)


# ---------------------------------------------------------------------------
# W280 packet-size truncation steps (deterministic, frozen order)
# ---------------------------------------------------------------------------
#
# Each step is a callable ``(packet) -> (new_packet, fired_bool)``. A step
# returns ``(packet, False)`` when there is nothing to drop in that step
# (so the orchestrator can move to the next step without changing the
# packet); otherwise it returns a new packet with the relevant payload
# cleared plus ``True`` so the orchestrator knows to stamp
# ``size_limit`` on ``redactions``.
#
# The steps are declared as plain functions so the tuple is readable
# top-to-bottom; the orchestrator iterates ``_BUDGET_TRUNCATION_STEPS``
# in declared order.


def _step_drop_artifact_content_inline(
    packet: ChangeEvidence,
) -> tuple[ChangeEvidence, bool]:
    """Drop ``content_inline`` from every artifact that has one set."""
    if not any(a.content_inline is not None for a in packet.artifacts):
        return packet, False
    new_artifacts = tuple(
        dataclasses.replace(a, content_inline=None) if a.content_inline is not None else a for a in packet.artifacts
    )
    return dataclasses.replace(packet, artifacts=new_artifacts), True


def _step_drop_context_ref_content_inline(
    packet: ChangeEvidence,
) -> tuple[ChangeEvidence, bool]:
    """Drop ``content_inline`` from every context_ref that has one set."""
    if not any(c.content_inline is not None for c in packet.context_refs):
        return packet, False
    new_refs = tuple(
        dataclasses.replace(c, content_inline=None) if c.content_inline is not None else c for c in packet.context_refs
    )
    return dataclasses.replace(packet, context_refs=new_refs), True


def _step_drop_policy_decision_extra(
    packet: ChangeEvidence,
) -> tuple[ChangeEvidence, bool]:
    """Clear the ``extra`` mapping on every PolicyDecision row.

    Legacy dict rows (pre-W279 free-form mappings) keep their shape but
    have their nested non-first-class keys preserved as-is; the dict
    rows can't carry a separate ``extra`` payload, so this step is a
    no-op against them.
    """
    fired = False
    new_pd: list[PolicyDecision | Mapping[str, Any]] = []
    for row in packet.policy_decisions:
        if isinstance(row, PolicyDecision) and row.extra:
            new_pd.append(dataclasses.replace(row, extra={}))
            fired = True
        else:
            new_pd.append(row)
    if not fired:
        return packet, False
    return dataclasses.replace(packet, policy_decisions=tuple(new_pd)), True


def _step_drop_finding_evidence(
    packet: ChangeEvidence,
) -> tuple[ChangeEvidence, bool]:
    """Clear ``evidence`` sub-dicts on finding rows that carry one.

    Phase-1 findings are typed as ``Mapping[str, Any]`` so producers
    may attach a free-form ``evidence`` key for supplementary detail.
    If no row carries the key, the step is a no-op.
    """
    fired = False
    new_findings: list[Mapping[str, Any]] = []
    for row in packet.findings:
        if isinstance(row, Mapping) and isinstance(row.get("evidence"), Mapping):
            mutated = dict(row)
            mutated["evidence"] = {}
            new_findings.append(mutated)
            fired = True
        else:
            new_findings.append(row)
    if not fired:
        return packet, False
    return dataclasses.replace(packet, findings=tuple(new_findings)), True


def _step_drop_actor_ref_extra(
    packet: ChangeEvidence,
) -> tuple[ChangeEvidence, bool]:
    """Clear the ``extra`` mapping on every ActorRef that has one populated."""
    if not any(bool(a.extra) for a in packet.actor_refs):
        return packet, False
    new_refs = tuple(dataclasses.replace(a, extra={}) if a.extra else a for a in packet.actor_refs)
    return dataclasses.replace(packet, actor_refs=new_refs), True


#: Frozen tuple of truncation steps. Order is the W280 contract;
#: re-ordering is a breaking change for any consumer relying on the
#: documented dropping sequence.
_BUDGET_TRUNCATION_STEPS: tuple[Any, ...] = (
    _step_drop_artifact_content_inline,
    _step_drop_context_ref_content_inline,
    _step_drop_policy_decision_extra,
    _step_drop_finding_evidence,
    _step_drop_actor_ref_extra,
)


def packet_size_bytes(packet_obj: Mapping[str, Any] | ChangeEvidence) -> int:
    """Return the canonical-JSON byte length of a packet.

    Accepts either a ``ChangeEvidence`` dataclass instance or a raw dict
    (e.g. one parsed from disk by ``cmd_evidence_doctor``). For
    dataclass instances, uses :meth:`ChangeEvidence.to_canonical_json`
    so the byte count matches what would be written to the wire. For
    raw dicts, applies the same W182/W210 omission rules + canonical
    JSON settings as :meth:`ChangeEvidence.to_canonical_json` so the
    doctor's reading matches what the producer would have computed.
    """
    if isinstance(packet_obj, ChangeEvidence):
        return len(packet_obj.to_canonical_json().encode("utf-8"))
    # Raw dict path: mirror the omission rules so the byte count tracks
    # what the dataclass canonicaliser would emit for the same payload.
    stripped = dict(packet_obj)
    for k in _W182_OMIT_WHEN_EMPTY_FIELDS:
        if stripped.get(k) == []:
            stripped.pop(k, None)
    for k, default in _W210_OMIT_WHEN_DEFAULT_FIELDS.items():
        if k in stripped and stripped[k] == default:
            stripped.pop(k, None)
    try:
        canonical = json.dumps(stripped, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        # Defensive: a raw dict that isn't JSON-serialisable shouldn't
        # reach here (the doctor parsed it from JSON), but return 0
        # rather than crashing so the doctor can still render its other
        # diagnostics.
        return 0
    return len(canonical.encode("utf-8"))


def classify_packet_budget(size_bytes: int) -> str:
    """Classify a canonical-JSON byte count against the W280 budget.

    Returns one of :data:`PACKET_BUDGET_STATES`:

    * ``"within_budget"`` when the packet fits ``PACKET_SIZE_BUDGET_BYTES``.
    * ``"oversized_after_truncation"`` when the packet is over budget
      (the doctor uses this state for any over-budget on-disk packet -
      after truncation has already had its chance to fire during
      :meth:`ChangeEvidence.with_content_hash`, a stored packet that
      still exceeds the budget is by definition "oversized after
      truncation").

    The ``"truncated"`` state is reserved for producers calling
    :meth:`ChangeEvidence._apply_size_budget` directly and observing
    that truncation fired AND the resulting size fits the budget. The
    doctor cannot distinguish "this was always small" from "this was
    truncated down to small" from inspecting the packet alone, so it
    surfaces ``size_limit`` in ``redactions`` as the marker for the
    truncated case.
    """
    if size_bytes <= PACKET_SIZE_BUDGET_BYTES:
        return "within_budget"
    return "oversized_after_truncation"


# ``_parse_iso`` was previously duplicated here; it now lives on
# :mod:`roam.evidence.approval` and is imported at the top of this
# module (W880). ``approval`` is a leaf under ``roam.evidence`` and
# does not import from ``change_evidence``, so there is no import cycle.


__all__ = [
    "ChangeEvidence",
    "EVIDENCE_SCHEMA_VERSION",
    "PACKET_SIZE_BUDGET_BYTES",
    "PACKET_BUDGET_STATES",
    "resolve_roam_version",
    "classify_packet_budget",
    "packet_size_bytes",
    "stale_accepted_risks",
]
