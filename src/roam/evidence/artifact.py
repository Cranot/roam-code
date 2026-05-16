"""``EvidenceArtifact`` - a generated artifact (report, SARIF,
attestation, ...) referenced by an evidence packet.

Large artifacts MUST be referenced by ``path`` + ``content_hash`` rather
than embedded via ``content_inline``. The architecture memo's redaction
section (lines 265-279) and Phase 1 done-condition (line 372) both
require this: ``content_inline`` is for small payloads only, and a
soft-conformance check in ``tests/test_evidence_v0.py`` enforces the
distinction.

NON-GOALS:

* No raw secrets. ``content_inline`` MUST NOT carry credential
  material; any artifact whose contents include secrets must be
  redacted at production time (record the redaction reason in
  ``redactions[]`` using the ``"secret"`` enum entry).
* No source snippets larger than ``INLINE_CONTENT_SOFT_LIMIT_BYTES``
  (8 KiB). Callers exceeding the limit must write the bytes to disk
  and reference via ``path`` + ``content_hash`` instead.
"""

from __future__ import annotations

import dataclasses
import warnings
from collections.abc import Mapping
from typing import Any

from roam.evidence._vocabulary import ARTIFACT_KINDS, REDACTION_REASONS

# Per-artifact advisory ceiling for ``content_inline`` payload bytes.
#
# WHAT IT IS:
#   A recommended cap (8 KiB) on the size of a SINGLE artifact's inline
#   content. Producers SHOULD consult this constant when deciding
#   whether to embed an artifact's bytes via ``content_inline`` vs.
#   write the bytes to disk and reference via ``path`` + ``content_hash``.
#   The soft-conformance test in ``tests/test_evidence_v0.py`` exercises
#   the recommended pattern (large artifact -> path-referenced).
#
# WHAT IT IS NOT:
#   NOT an enforced limit. The ``EvidenceArtifact`` constructor will
#   accept ``content_inline`` of any size; no consumer currently rejects
#   or trims an oversized inline payload at this layer. The 8 KiB value
#   is a per-artifact UPSTREAM PRESSURE SIGNAL - guidance for producers
#   to bound individual artifacts before they accumulate.
#
# DISTINCT FROM ``PACKET_SIZE_BUDGET_BYTES`` (W280):
#   ``PACKET_SIZE_BUDGET_BYTES`` (256 KiB) is the ENFORCED packet-level
#   budget applied to the canonical-JSON serialisation of the WHOLE
#   ``ChangeEvidence`` packet inside ``with_content_hash``. When that
#   budget trips, the deterministic truncation pipeline drops
#   ``artifacts[].content_inline`` FIRST (step 1 of 5), so this 8 KiB
#   per-artifact ceiling is the upstream pressure signal that keeps
#   producers below the downstream enforced cap. The two limits operate
#   at different scopes with different semantics:
#     * 8 KiB    - per artifact, advisory, no runtime enforcement
#     * 256 KiB  - per packet, enforced, deterministic truncation
#
# W288-followup: emit a producer-side warning when inline content exceeds
# this ceiling. The limit remains advisory: the constructor accepts the
# artifact unchanged, does not truncate, and does not stamp redactions.
#
# See also: PACKET_SIZE_BUDGET_BYTES (W280) in
# ``src/roam/evidence/change_evidence.py`` for the enforced packet-level
# limit and its truncation pipeline.
INLINE_CONTENT_SOFT_LIMIT_BYTES: int = 8 * 1024  # 8 KiB


@dataclasses.dataclass(frozen=True)
class EvidenceArtifact:
    """One generated artifact referenced by an evidence packet.

    Fields:

    * ``artifact_id`` - stable identifier. Convention:
      ``"<kind>:<short-hash>"`` (e.g. ``"sarif:7f3a9c1"``). Consumers
      should treat it as opaque.
    * ``kind`` - one of ``ARTIFACT_KINDS``. Validated.
    * ``path`` - filesystem path relative to ``.roam/`` (or another
      well-known anchor). Preferred over ``content_inline`` for any
      artifact bigger than ``INLINE_CONTENT_SOFT_LIMIT_BYTES``.
    * ``content_hash`` - hex-encoded sha256 of the artifact bytes.
      MUST be populated whenever ``path`` is populated, so consumers
      can verify the on-disk content hasn't drifted.
    * ``content_inline`` - small artifact body embedded directly.
      Mutually exclusive with ``path`` at the validation level: a
      caller passes EITHER ``path`` (with ``content_hash``) OR
      ``content_inline``, never both. The constructor enforces this.
    * ``redactions`` - tuple of reasons (from ``REDACTION_REASONS``)
      explaining what was masked. The empty tuple means "nothing
      redacted"; consumers should not interpret missing-vs-empty
      differently.
    * ``extra`` - free-form structured detail.

    Frozen so artifacts can be hashed / used as dict keys.
    """

    artifact_id: str
    kind: str
    path: str | None = None
    content_hash: str | None = None
    content_inline: str | None = None
    redactions: tuple[str, ...] = ()
    extra: Mapping[str, Any] = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.artifact_id, str) or not self.artifact_id:
            raise ValueError("EvidenceArtifact.artifact_id must be a non-empty string")
        if self.kind not in ARTIFACT_KINDS:
            raise ValueError(f"EvidenceArtifact.kind={self.kind!r} is not in ARTIFACT_KINDS")

        # Mutual exclusion: path + content_inline cannot both be set.
        # An artifact is either referenced by path (preferred for big
        # blobs) or inlined (small blobs only); having both creates an
        # ambiguity about which is authoritative.
        if self.path is not None and self.content_inline is not None:
            raise ValueError(
                "EvidenceArtifact: path and content_inline are mutually exclusive (use one or the other, not both)"
            )

        # When ``path`` is set, ``content_hash`` MUST be set too — the
        # whole point of the path-referenced form is that the consumer
        # can verify the on-disk bytes match what the packet claims.
        if self.path is not None and not self.content_hash:
            raise ValueError(
                "EvidenceArtifact: path requires content_hash (sha256 hex) so consumers can verify on-disk integrity"
            )

        # Redaction reasons must be drawn from the closed enumeration.
        for reason in self.redactions:
            if reason not in REDACTION_REASONS:
                raise ValueError(
                    f"EvidenceArtifact.redactions: unknown reason {reason!r}; must be one of REDACTION_REASONS"
                )

        if self.content_inline is not None:
            inline_bytes = len(self.content_inline.encode("utf-8", errors="replace"))
            if inline_bytes > INLINE_CONTENT_SOFT_LIMIT_BYTES:
                warnings.warn(
                    "EvidenceArtifact.content_inline exceeds "
                    "INLINE_CONTENT_SOFT_LIMIT_BYTES; prefer path + "
                    "content_hash for large artifacts",
                    UserWarning,
                    stacklevel=2,
                )


__all__ = ["EvidenceArtifact", "INLINE_CONTENT_SOFT_LIMIT_BYTES"]
