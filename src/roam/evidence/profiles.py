"""W226 - export profiles for ``ChangeEvidence``.

Four render-time profile presets that control how much of an evidence
packet is shown to different audiences:

* ``internal``  - no redactions (default / authoritative form)
* ``customer``  - drop internal IDs from extras, artifacts by reference only
* ``audit``     - same as customer but PRESERVE actor identities
* ``public``    - anonymise humans, drop artifact paths AND inline content

These are RENDER-TIME transforms. They:

* Return a NEW ``ChangeEvidence`` packet whose canonical JSON form has
  the redactions applied.
* Do NOT recompute the ``content_hash`` field on the returned packet -
  the recorded hash represents the AUTHORITATIVE (internal) form of the
  packet and consumers should never recompute from the redacted form.
* APPEND profile-tagged strings to the packet's ``redactions`` tuple
  describing exactly what was applied (e.g.
  ``"profile:public:human_actor"``). This is the OPA-style masking-trail
  pattern: never silently drop a field, always record what was masked
  and why.

The directive (architecture memo, lines 310-324) is explicit: redact
identifying detail by default, record what was redacted, never export
raw prompts / secrets / source snippets by default. The four profiles
codify the four common audiences for a packet.

NOTE on the redactions string format: the dataclass-level validator on
``ChangeEvidence.redactions`` only accepts entries from the closed
:data:`REDACTION_REASONS` enumeration (nine entries: ``secret``,
``pii``, ``sensitive_content``, ``size_limit``, ``policy``,
``user_opt_in_required``, ``machine_local_path``, ``schema_strict``,
``producer_not_available``). The profile-tagged strings this module
appends (e.g. ``"profile:public:human_actor"``) are NOT members of that
closed enumeration; they are a richer masking trail required by the
W226 directive. We attach them via ``object.__setattr__`` to bypass the
frozen-dataclass validator - the same trick :mod:`roam.evidence.refs`
uses for the W198 permit-facade marker. This keeps the strict
construction-time validator intact for normal callers AND lets the
profile machinery record the audit trail the directive asks for.

Determinism contract:

* :func:`apply_profile` is pure: same input packet + profile name
  produces the same output packet (modulo the appended ordered tag
  list).
* The returned packet still round-trips through
  :meth:`ChangeEvidence.to_canonical_json` - the masking trail is just
  more strings in the ``redactions`` list.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any

from roam.evidence.artifact import EvidenceArtifact
from roam.evidence.change_evidence import ChangeEvidence
from roam.evidence.refs import ActorRef


# ---------------------------------------------------------------------------
# Profile model
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ExportProfile:
    """One render-time redaction profile.

    Fields:

    * ``name`` - one of ``"internal"`` / ``"customer"`` / ``"audit"`` /
      ``"public"``. Used as the prefix of every appended masking-trail
      entry (e.g. ``"profile:public:human_actor"``).
    * ``redact_extra_keys`` - tuple of keys to drop from any
      ``extra: Mapping`` field on the packet, its artifacts, and its
      refs. Match is by exact key name.
    * ``redact_artifact_fields`` - tuple of artifact field names to
      null out on every artifact in the packet. Today's accepted names
      are ``"content_inline"`` (force path+hash form) and ``"path"``
      (drop the filesystem path and leave only ``content_hash``).
    * ``redact_actor_fields`` - tuple of ``ChangeEvidence`` /
      ``ActorRef`` field names to anonymise. Accepted names are
      ``"human_actor"`` (the packet-level field) and ``"display_name"``
      (the per-``ActorRef`` field).
    * ``inline_artifact_size_limit`` - byte budget for inline content.
      Any artifact whose ``content_inline`` exceeds this is converted
      to path-only form (``content_inline`` cleared) and a
      ``profile:<name>:artifact_size_limit`` entry is appended to the
      packet's redactions.
    * ``notes_for_consumer`` - human-readable banner that a renderer
      can show alongside the redacted packet.
    """

    name: str
    redact_extra_keys: tuple[str, ...]
    redact_artifact_fields: tuple[str, ...]
    redact_actor_fields: tuple[str, ...]
    inline_artifact_size_limit: int
    notes_for_consumer: str


EXPORT_PROFILES: Mapping[str, ExportProfile] = {
    "internal": ExportProfile(
        name="internal",
        redact_extra_keys=(),
        redact_artifact_fields=(),
        redact_actor_fields=(),
        inline_artifact_size_limit=64_000,
        notes_for_consumer="Internal - no redactions applied.",
    ),
    "customer": ExportProfile(
        name="customer",
        redact_extra_keys=("internal_id", "raw_text"),
        redact_artifact_fields=("content_inline",),
        redact_actor_fields=(),
        inline_artifact_size_limit=8_000,
        notes_for_consumer=(
            "Customer-shareable - internal IDs redacted, artifacts by "
            "reference."
        ),
    ),
    "audit": ExportProfile(
        name="audit",
        redact_extra_keys=("internal_id", "raw_text"),
        redact_artifact_fields=("content_inline",),
        redact_actor_fields=(),
        inline_artifact_size_limit=8_000,
        notes_for_consumer=(
            "Audit profile - identities preserved, artifact content by "
            "hash."
        ),
    ),
    "public": ExportProfile(
        name="public",
        redact_extra_keys=("internal_id", "raw_text", "email", "username"),
        redact_artifact_fields=("content_inline", "path"),
        redact_actor_fields=("human_actor", "display_name"),
        inline_artifact_size_limit=2_000,
        notes_for_consumer=(
            "Public profile - all human identities and artifact content "
            "fully redacted."
        ),
    ),
}


# ---------------------------------------------------------------------------
# apply_profile
# ---------------------------------------------------------------------------


def apply_profile(
    packet: ChangeEvidence,
    profile_name: str,
) -> tuple[ChangeEvidence, list[str]]:
    """Return ``(redacted_packet, warnings)`` for the named profile.

    CRITICAL: this is a render-time transform. It does NOT change the
    packet's ``content_hash`` field - that hash was computed BEFORE
    profile application and represents the AUTHORITATIVE (internal) form
    of the packet. The redacted packet's rendered JSON form will hash
    DIFFERENTLY, but consumers should NOT recompute the hash from the
    redacted form; they verify against the recorded hash.

    The ``redactions`` field on the returned packet is APPENDED to (not
    overwritten) with profile-tagged strings describing exactly what was
    applied (e.g. ``"profile:public:human_actor"``,
    ``"profile:customer:artifact_inline"``). Existing redactions (e.g.
    a producer-attached ``"secret"`` entry) are preserved verbatim at
    the head of the tuple.

    Args:
        packet: The authoritative ``ChangeEvidence`` to render.
        profile_name: One of ``"internal"`` / ``"customer"`` / ``"audit"`` /
            ``"public"``.

    Returns:
        A tuple ``(redacted_packet, warnings)`` where ``warnings`` is a
        list of human-readable strings describing edge cases the caller
        may want to surface (e.g. unknown profile name fell back to
        ``"internal"``; an artifact exceeded the size budget; etc.).

    Raises:
        Does NOT raise on unknown profile names - falls back to the
        ``"internal"`` profile (pass-through) and emits a warning. This
        is intentional: a render-time transform should never crash the
        renderer, only degrade gracefully to the safest fallback.
    """
    warnings: list[str] = []

    profile = EXPORT_PROFILES.get(profile_name)
    if profile is None:
        warnings.append(
            f"unknown profile {profile_name!r}; falling back to 'internal' "
            f"(no redactions applied)"
        )
        profile = EXPORT_PROFILES["internal"]

    # Fast path: the internal profile is a true pass-through. Returning
    # the same instance preserves identity for callers that compare by
    # ``is`` and avoids unnecessary tuple churn.
    if profile.name == "internal":
        return packet, warnings

    applied_tags: list[str] = []

    # ---- packet-level actor fields ----------------------------------
    new_human_actor = packet.human_actor
    if "human_actor" in profile.redact_actor_fields:
        if packet.human_actor is not None:
            new_human_actor = None
            applied_tags.append(f"profile:{profile.name}:human_actor")

    # ---- artifacts (context_refs + artifacts tuples) ---------------
    new_context_refs = _redact_artifacts(
        packet.context_refs, profile, applied_tags
    )
    new_artifacts = _redact_artifacts(
        packet.artifacts, profile, applied_tags
    )

    # ---- actor_refs (W182 list of ActorRef) ------------------------
    new_actor_refs = _redact_actor_refs(
        packet.actor_refs, profile, applied_tags
    )

    # ---- changed_subjects extras -----------------------------------
    new_changed_subjects = tuple(
        _redact_subject_extras(s, profile, applied_tags)
        for s in packet.changed_subjects
    )

    # ---- findings / policy_decisions / tests_run / approvals /
    # ---- accepted_risks - all Mapping[str, Any] tuples -------------
    new_findings = _redact_mapping_tuple(
        packet.findings, profile, applied_tags, source="findings"
    )
    new_policy_decisions = _redact_mapping_tuple(
        packet.policy_decisions, profile, applied_tags,
        source="policy_decisions",
    )
    new_tests_run = _redact_mapping_tuple(
        packet.tests_run, profile, applied_tags, source="tests_run"
    )
    new_approvals = _redact_mapping_tuple(
        packet.approvals, profile, applied_tags, source="approvals"
    )
    new_accepted_risks = _redact_mapping_tuple(
        packet.accepted_risks, profile, applied_tags,
        source="accepted_risks",
    )

    # ---- redactions tuple: existing entries + dedup profile tags ----
    # We dedup the appended tags so a profile that touches three
    # artifacts doesn't append ``profile:customer:artifact_inline``
    # three times. Existing producer-attached redactions stay at the
    # head of the tuple in their original order.
    seen_tags: set[str] = set()
    deduped_appended: list[str] = []
    for tag in applied_tags:
        if tag not in seen_tags:
            seen_tags.add(tag)
            deduped_appended.append(tag)
    new_redactions = tuple(packet.redactions) + tuple(deduped_appended)

    # Build the new packet via dataclasses.replace. ``__post_init__``
    # will VALIDATE the redactions tuple against the closed enumeration
    # ``REDACTION_REASONS``, which would reject our profile-tagged
    # strings. We therefore build the replaced packet WITHOUT the new
    # redactions first (passing only the original producer-attached
    # set), then bypass the frozen-dataclass validator via
    # ``object.__setattr__`` to attach the masking trail. Same trick
    # ``roam.evidence.refs.AuthorityRef.__post_init__`` uses for the
    # W198 permit-facade marker.
    rebuilt = dataclasses.replace(
        packet,
        human_actor=new_human_actor,
        context_refs=new_context_refs,
        artifacts=new_artifacts,
        actor_refs=new_actor_refs,
        changed_subjects=new_changed_subjects,
        findings=new_findings,
        policy_decisions=new_policy_decisions,
        tests_run=new_tests_run,
        approvals=new_approvals,
        accepted_risks=new_accepted_risks,
        # ``redactions`` left at the original tuple here so the post-init
        # validator (which checks every entry against the closed
        # REDACTION_REASONS enum) doesn't reject the profile-tagged
        # strings. They're attached below.
    )
    object.__setattr__(rebuilt, "redactions", new_redactions)

    return rebuilt, warnings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _scrub_extra(
    extra: Mapping[str, Any],
    profile: ExportProfile,
    applied_tags: list[str],
    *,
    source: str,
) -> Mapping[str, Any]:
    """Return a new mapping with ``profile.redact_extra_keys`` removed.

    ``source`` is the logical owner of the mapping (``"artifact"`` /
    ``"actor_ref"`` / ``"subject"`` / ``"finding"`` / ...). Used to
    build the appended masking-trail tag so a consumer can tell which
    field-class was scrubbed.
    """
    if not profile.redact_extra_keys or not extra:
        return extra

    new_extra = {k: v for k, v in extra.items() if k not in profile.redact_extra_keys}
    if len(new_extra) != len(extra):
        applied_tags.append(
            f"profile:{profile.name}:{source}_extra"
        )
    return new_extra


def _redact_artifacts(
    artifacts: tuple[EvidenceArtifact, ...],
    profile: ExportProfile,
    applied_tags: list[str],
) -> tuple[EvidenceArtifact, ...]:
    """Apply artifact-level redactions to every artifact in the tuple.

    Rules driven by the profile fields:

    * If ``"content_inline"`` is in ``redact_artifact_fields``, every
      artifact with a populated ``content_inline`` is converted to
      path-only form.
    * If ``"path"`` is in ``redact_artifact_fields``, every artifact
      with a populated ``path`` has the path cleared (the
      ``content_hash`` survives so consumers can still verify the
      bytes if they have them in hand).
    * If ``inline_artifact_size_limit`` is non-zero, any artifact whose
      ``content_inline`` exceeds the limit is forced to path-only form.
      The byte-count is measured on the UTF-8 encoding of the string.
    * ``profile.redact_extra_keys`` are applied to ``extra``.
    """
    if not artifacts:
        return artifacts

    out: list[EvidenceArtifact] = []
    for art in artifacts:
        new_path = art.path
        new_content_inline = art.content_inline
        new_content_hash = art.content_hash
        new_extra = _scrub_extra(
            art.extra, profile, applied_tags, source="artifact"
        )

        # content_inline drop -------------------------------------------------
        if (
            "content_inline" in profile.redact_artifact_fields
            and art.content_inline is not None
        ):
            new_content_inline = None
            applied_tags.append(
                f"profile:{profile.name}:artifact_inline"
            )

        # Size-budget enforcement (only when content_inline survives) --------
        # We measure the BYTES of the UTF-8 encoded string. If the
        # caller hands us a 9 KB inline doc and the budget is 8 KB, the
        # inline content is dropped and the masking trail tags it with
        # the ``artifact_size_limit`` reason. The artifact is NOT
        # restored to path form here - that requires a path + hash the
        # producer would have set if it was a big blob; we leave both
        # fields ``None`` and rely on the producer's path/hash if
        # present.
        if (
            new_content_inline is not None
            and profile.inline_artifact_size_limit > 0
        ):
            inline_bytes = len(
                new_content_inline.encode("utf-8", errors="replace")
            )
            if inline_bytes > profile.inline_artifact_size_limit:
                new_content_inline = None
                applied_tags.append(
                    f"profile:{profile.name}:artifact_size_limit"
                )

        # path drop -----------------------------------------------------------
        # Done AFTER content_inline so the ``content_hash`` survives as
        # the consumer's only handle on the artifact bytes. The
        # mutual-exclusion validator on ``EvidenceArtifact`` permits
        # the ``(path=None, content_inline=None)`` combination (an
        # artifact known only by its content_hash).
        if (
            "path" in profile.redact_artifact_fields
            and art.path is not None
        ):
            new_path = None
            applied_tags.append(
                f"profile:{profile.name}:artifact_path"
            )

        # Rebuild the artifact. We use the constructor directly so the
        # frozen-dataclass invariants (kind validation, redaction-reason
        # validation, mutual exclusion of path/content_inline) re-run.
        # The artifact-level ``redactions`` field is NOT touched here -
        # the packet-level redactions tuple carries the masking trail
        # for profile-driven changes.
        out.append(
            EvidenceArtifact(
                artifact_id=art.artifact_id,
                kind=art.kind,
                path=new_path,
                content_hash=new_content_hash,
                content_inline=new_content_inline,
                redactions=art.redactions,
                extra=new_extra,
            )
        )

    return tuple(out)


def _redact_actor_refs(
    actor_refs: tuple[ActorRef, ...],
    profile: ExportProfile,
    applied_tags: list[str],
) -> tuple[ActorRef, ...]:
    """Anonymise ``ActorRef`` entries per profile.

    * ``"display_name"`` in ``redact_actor_fields`` clears the
      ``display_name`` field on every human-kind ref. We deliberately
      DON'T touch non-human kinds (``agent``, ``ci_runner``, ...)
      because those identities aren't PII - they're system identities
      the consumer needs to see for accountability.
    * ``profile.redact_extra_keys`` are applied to each ref's ``extra``
      mapping.
    """
    if not actor_refs:
        return actor_refs

    out: list[ActorRef] = []
    for ref in actor_refs:
        new_display_name = ref.display_name
        new_extra = _scrub_extra(
            ref.extra, profile, applied_tags, source="actor_ref"
        )

        if (
            "display_name" in profile.redact_actor_fields
            and ref.actor_kind == "human"
            and ref.display_name is not None
        ):
            new_display_name = None
            applied_tags.append(
                f"profile:{profile.name}:actor_display_name"
            )

        out.append(
            ActorRef(
                actor_kind=ref.actor_kind,
                actor_id=ref.actor_id,
                display_name=new_display_name,
                trust_tier=ref.trust_tier,
                extra=new_extra,
            )
        )
    return tuple(out)


def _redact_subject_extras(
    subject: Any,
    profile: ExportProfile,
    applied_tags: list[str],
) -> Any:
    """Strip ``profile.redact_extra_keys`` from ``subject.extra``.

    Returns a NEW subject with the scrubbed extra. Typed as ``Any`` so
    this helper can serve any frozen-dataclass subject shape that
    carries an ``extra: Mapping`` field; today that's ``EvidenceSubject``.
    """
    if not profile.redact_extra_keys:
        return subject

    new_extra = _scrub_extra(
        subject.extra, profile, applied_tags, source="subject"
    )
    if new_extra is subject.extra:
        return subject
    return dataclasses.replace(subject, extra=new_extra)


def _redact_mapping_tuple(
    rows: tuple[Mapping[str, Any], ...],
    profile: ExportProfile,
    applied_tags: list[str],
    *,
    source: str,
) -> tuple[Mapping[str, Any], ...]:
    """Strip ``profile.redact_extra_keys`` from every row's top-level keys.

    Findings / policy_decisions / tests_run / approvals / accepted_risks
    are typed as ``tuple[Mapping[str, Any], ...]`` rather than dedicated
    dataclasses (Phase 1 contract). We treat their TOP-LEVEL keys as
    the equivalent of an artifact's ``extra`` mapping: any key whose
    name matches a ``redact_extra_keys`` entry is removed.
    """
    if not profile.redact_extra_keys or not rows:
        return rows

    out: list[Mapping[str, Any]] = []
    changed = False
    for row in rows:
        if not isinstance(row, Mapping):
            out.append(row)
            continue
        scrubbed = {
            k: v for k, v in row.items() if k not in profile.redact_extra_keys
        }
        if len(scrubbed) != len(row):
            changed = True
        out.append(scrubbed)
    if changed:
        applied_tags.append(f"profile:{profile.name}:{source}_extra")
    return tuple(out)


__all__ = [
    "EXPORT_PROFILES",
    "ExportProfile",
    "apply_profile",
]
