"""SLSA v1.2 Verification Summary Attestation (VSA) predicate wrapper.

W451 - thin wrapper that projects :class:`ChangeEvidence` into the
SLSA v1 VSA predicate shape so that ``slsa-verifier`` / Sigstore /
Rekor consumers can ingest roam attestations without learning the
roam-specific ``CodeGraph/v1`` predicate.

Source-of-truth: in-toto v1 Statement + SLSA Source VSA v1.

* in-toto v1 Statement envelope:
  ``_type: https://in-toto.io/Statement/v1``,
  ``subject: [{name, digest: {...}}]``, ``predicateType``, ``predicate``.
* SLSA VSA v1 predicateType: ``https://slsa.dev/verification_summary/v1``.
* SLSA VSA v1 predicate fields (per
  https://slsa.dev/spec/v1.0/verification_summary): ``verifier``,
  ``timeVerified``, ``resourceUri``, ``policy``, ``inputAttestations``,
  ``verificationResult``, ``verifiedLevels``, ``dependencyLevels``,
  ``slsaVersion``.

Two predicate shapes produced here:

1. :func:`build_vsa_statement` - one VSA per :class:`ChangeEvidence`
   packet. ``resourceUri`` is the git commit; ``inputAttestations``
   reference the underlying CGA + the ChangeEvidence content hash;
   ``verifiedLevels`` is ``["SLSA_SOURCE_LEVEL_3"]`` when the assurance
   floor is met, otherwise the actual floor.
2. :func:`build_run_ledger_root_statement` - second attestation rooted
   at the HMAC ``final_signature`` of an active run. Lets an external
   verifier anchor trust in the run-ledger chain WITHOUT replaying
   every event. ``predicateType`` is roam-specific
   (``https://roam-code.com/spec/RunLedgerRoot/v1``) because no
   external standard covers "HMAC root of an append-only event chain".

Hash-stability contract: this module is ADDITIVE. It consumes
``ChangeEvidence`` read-only and produces new in-toto Statement
dicts; the source dataclass and its canonical JSON are untouched.

Wording-lint: every VSA produced here carries the
``"slsa.dev/verification_summary/v1"`` predicate type. The accompanying
human-facing reports MUST say "supports evidence for SLSA SRC-L3" and
NEVER "certifies SLSA SRC-L3 compliance" (roam emits the evidence; the
verifier asserts the claim).
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from roam.evidence.change_evidence import ChangeEvidence

# Public constants - consumers import these for type-checking emitted
# predicates without re-deriving the URIs.
STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
SLSA_VSA_PREDICATE_TYPE = "https://slsa.dev/verification_summary/v1"
SLSA_VERSION = "1.2"

# roam-specific run-ledger-root predicate type. Owned by roam-code.com
# because no SLSA / in-toto predicate covers "HMAC final signature of
# an append-only per-run event ledger". The shape is documented inline
# in :func:`build_run_ledger_root_predicate` and at
# https://roam-code.com/spec/RunLedgerRoot/v1 (alias of the inline
# schema). Verifiers that don't know this type SHOULD ignore the
# attestation rather than fail-closed (additive evidence).
RUN_LEDGER_ROOT_PREDICATE_TYPE = "https://roam-code.com/spec/RunLedgerRoot/v1"

# Closed enumeration of SLSA Source levels per v1.2 source-track spec.
# Maps roam's assurance-floor + completeness signals into the level
# string the SLSA verifier wants. Roam does NOT issue SRC_L4 today
# (approvals harvesting is partial; see SLSA-V12-POSITIONING memo
# Gap D), so ``_SLSA_LEVELS`` deliberately stops at L3.
_SLSA_LEVELS = (
    "SLSA_SOURCE_LEVEL_1",
    "SLSA_SOURCE_LEVEL_2",
    "SLSA_SOURCE_LEVEL_3",
)


def _utc_now_iso() -> str:
    """RFC 3339 timestamp at second granularity. Same shape as the rest
    of the attest package (``cga.py``)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _resource_uri(change_evidence: ChangeEvidence) -> str:
    """Build the ``resourceUri`` for the VSA subject.

    Preferred shape: ``git+<repo_id>@<commit_sha>`` per Sigstore's
    convention for git resources. Falls back to ``<repo_id>`` alone
    when no commit SHA is known, or to ``urn:roam:evidence:<id>`` when
    neither is populated. The fallback chain keeps the VSA emit path
    total - a ChangeEvidence packet without a repo identity still
    produces a structurally valid SLSA VSA, just with weaker linkage.
    """
    repo = change_evidence.repo_id
    sha = change_evidence.commit_sha
    if repo and sha:
        return f"git+{repo}@{sha}"
    if repo:
        return str(repo)
    return f"urn:roam:evidence:{change_evidence.evidence_id}"


def _subject_digest(change_evidence: ChangeEvidence) -> dict[str, str]:
    """Build the in-toto subject digest from the ChangeEvidence.

    Carries the git commit SHA when present (``sha1`` per Git convention)
    AND the ChangeEvidence content_hash (``sha256``) so a verifier can
    bind the SLSA VSA to BOTH the source revision and the specific
    evidence packet that produced the verdict. Falls back to the
    content_hash alone when no commit SHA is known.
    """
    digest: dict[str, str] = {}
    if change_evidence.commit_sha:
        digest["sha1"] = change_evidence.commit_sha
    if change_evidence.content_hash:
        digest["sha256"] = change_evidence.content_hash
    if not digest:
        # Last-resort: hash the evidence_id so the subject is never
        # empty. A verifier seeing ``urn:...`` as the only key knows
        # the source identity was synthesised.
        digest["sha256"] = hashlib.sha256(change_evidence.evidence_id.encode("utf-8")).hexdigest()
    return digest


def _verified_levels(change_evidence: ChangeEvidence) -> list[str]:
    """Map ``ChangeEvidence.assurance_floor()`` to SLSA level strings.

    ``assurance_floor()`` returns
    ``{"passes": bool, "missing": tuple[str, ...],
       "stale": bool, "stale_reasons": tuple[str, ...]}``
    (W1254 added the ``stale`` / ``stale_reasons`` keys). This mapper
    intentionally reads ONLY ``passes`` and ``missing`` — coverage vs
    freshness are distinct assurance axes per W1254/W1261, and the
    sibling :func:`_verification_result` is the canonical consumer of
    ``stale`` (it downgrades stale-but-MVA-complete packets to
    ``FAILED``). Mapping:

    * ``passes=True`` (all six axes present: actor / authority /
      changed_subjects / findings / verification / policy_state) ->
      ``["SLSA_SOURCE_LEVEL_3"]``. The verifier can attest history,
      authority, AND continuous technical controls.
    * ``passes=False`` AND any of {``actor``, ``changed_subjects``} are
      present -> ``["SLSA_SOURCE_LEVEL_2"]``. Partial history /
      provenance coverage.
    * Otherwise -> ``["SLSA_SOURCE_LEVEL_1"]``. Revision exists, that's
      all we can attest.

    Roam does NOT issue L4 from VSA; SRC-L4 requires two-party review
    harvesting that ships in a later wave (see SLSA-V12-POSITIONING
    memo Gap D).
    """
    try:
        floor = change_evidence.assurance_floor() or {}
    except Exception:
        floor = {}
    if floor.get("passes"):
        return ["SLSA_SOURCE_LEVEL_3"]
    missing = set(floor.get("missing") or ())
    # L2 mapping: at minimum we need actor + changed_subjects present
    # (history + provenance axes). Strict subset of L3.
    if "actor" not in missing and "changed_subjects" not in missing:
        return ["SLSA_SOURCE_LEVEL_2"]
    return ["SLSA_SOURCE_LEVEL_1"]


def _input_attestations(change_evidence: ChangeEvidence) -> list[dict[str, Any]]:
    """Reference the upstream attestations that fed the verdict.

    Each entry is the SLSA ``inputAttestations`` shape: ``{uri, digest}``.
    We list:

    * The ChangeEvidence content_hash itself (urn:roam:evidence:<id>).
    * Any ``EvidenceArtifact`` with ``kind in (attestation,
      cga_predicate)`` - these are the underlying CGA + signed
      attestations the verdict was computed against.

    Empty list is valid SLSA - the verifier just learns the verdict
    was issued without referenced inputs.
    """
    inputs: list[dict[str, Any]] = []
    # Always include the ChangeEvidence packet itself as an input
    # attestation (the verdict-producing packet IS evidence).
    digest: dict[str, str] = {}
    if change_evidence.content_hash:
        digest["sha256"] = change_evidence.content_hash
    inputs.append(
        {
            "uri": f"urn:roam:evidence:{change_evidence.evidence_id}",
            "digest": digest or {"sha256": hashlib.sha256(change_evidence.evidence_id.encode("utf-8")).hexdigest()},
        }
    )
    # Add CGA + attestation artifacts.
    for artifact in change_evidence.artifacts:
        kind = getattr(artifact, "kind", None)
        if kind not in ("attestation", "cga_predicate"):
            continue
        artifact_digest: dict[str, str] = {}
        # ``EvidenceArtifact`` exposes ``content_hash`` (sha256) and
        # ``content_uri`` (path-or-urn). We project both into the
        # SLSA input-attestation shape.
        content_hash = getattr(artifact, "content_hash", None)
        if content_hash:
            artifact_digest["sha256"] = content_hash
        # ``EvidenceArtifact`` stores the on-disk reference under
        # ``path`` (not ``content_uri``). Fall back to a synthetic
        # ``urn:`` when neither path nor hash is populated so the
        # input-attestation entry is never structurally empty.
        artifact_path = getattr(artifact, "path", None)
        artifact_id = getattr(artifact, "artifact_id", None)
        if artifact_path:
            uri = str(artifact_path)
        elif artifact_id:
            uri = f"urn:roam:artifact:{artifact_id}"
        else:
            uri = f"urn:roam:artifact:{kind}"
        inputs.append({"uri": uri, "digest": artifact_digest})
    return inputs


def _verification_result(change_evidence: ChangeEvidence) -> str:
    """Map ChangeEvidence.verdict to the SLSA VSA verificationResult.

    SLSA VSA v1 documents two values: ``"PASSED"`` and ``"FAILED"``.
    PASSED requires ALL of:

    * ``assurance_floor().passes == True`` (the MVA gate cleared), AND
    * ``assurance_floor().stale != True`` (the evidence is fresh; W1261), AND
    * ``risk_level`` not in ``("high", "critical")``.

    Pattern-2 discipline: any error / missing floor / dangerous risk /
    stale evidence yields FAILED (explicit absence beats silent success).

    W1261 — stale-axis downgrade. The W210/W1254 staleness axis is a
    distinct quality signal from MVA-floor coverage: a packet can be
    MVA-complete (six axes populated) AND stale (context read predates
    edits). Pre-W1261 the VSA silently emitted ``PASSED`` on a stale-but-
    complete packet because ``_verification_result`` only read
    ``passes``. That re-introduced the Pattern-2 "silent success on
    degraded resolution" anti-pattern at the attestation boundary.
    Mirrors the existing high-risk downgrade: stale evidence is
    structurally analogous to high-risk evidence — both signal "do not
    trust the verifier verdict at face value."
    """
    try:
        floor = change_evidence.assurance_floor() or {}
    except Exception:
        return "FAILED"
    risk = (change_evidence.risk_level or "").lower()
    if risk in ("high", "critical"):
        return "FAILED"
    # W1261 - stale evidence cannot attest PASSED even when the MVA
    # floor passes. Checked BEFORE the ``passes`` gate so stale-but-
    # complete packets correctly degrade to FAILED.
    if floor.get("stale", False):
        return "FAILED"
    if floor.get("passes"):
        return "PASSED"
    return "FAILED"


def build_vsa_predicate(
    change_evidence: ChangeEvidence,
    *,
    verifier_id: str = "https://roam-code.com",
    policy_uri: str | None = None,
) -> dict[str, Any]:
    """Build the SLSA Source VSA predicate body from a ChangeEvidence.

    Pure - no I/O, no signing. Caller wraps the return value in an
    in-toto Statement via :func:`build_vsa_statement`.

    ``verifier_id`` defaults to ``https://roam-code.com`` (the tool
    that issued the verdict). Override when an outer verifier (CI,
    Chainloop, etc.) re-wraps this attestation.

    ``policy_uri`` is the URI of the policy the verdict was issued
    against. When the ChangeEvidence carries a ``constitution_hash``
    AND ``rules_config_hash`` we synthesise a ``urn:roam:policy:...``
    URI; otherwise the caller can pass an explicit one.
    """
    if policy_uri is None:
        # Synthesise from constitution + rules hashes when both are
        # known. The verifier can re-derive these hashes by reading
        # ``.roam/constitution.yml`` + the active rules config and
        # confirm policy identity without trusting roam.
        ch = change_evidence.constitution_hash
        rh = change_evidence.rules_config_hash
        if ch and rh:
            policy_uri = f"urn:roam:policy:constitution={ch[:12]}+rules={rh[:12]}"
        elif ch:
            policy_uri = f"urn:roam:policy:constitution={ch[:12]}"
        else:
            policy_uri = "urn:roam:policy:default"

    return {
        "verifier": {
            "id": verifier_id,
            # ``version`` is intentionally a sub-dict so multi-tool
            # verifiers can declare each component's version. roam
            # reports its own version when available.
            "version": {
                "roam-code": change_evidence.roam_version or "unknown",
            },
        },
        "timeVerified": change_evidence.completed_at or _utc_now_iso(),
        "resourceUri": _resource_uri(change_evidence),
        "policy": {"uri": policy_uri},
        "inputAttestations": _input_attestations(change_evidence),
        "verificationResult": _verification_result(change_evidence),
        "verifiedLevels": _verified_levels(change_evidence),
        "slsaVersion": SLSA_VERSION,
    }


def build_vsa_statement(
    change_evidence: ChangeEvidence,
    *,
    verifier_id: str = "https://roam-code.com",
    policy_uri: str | None = None,
) -> dict[str, Any]:
    """Wrap :func:`build_vsa_predicate` in an in-toto v1 Statement.

    Returns the full ``{_type, predicateType, subject, predicate}``
    dict ready to serialise with :func:`roam.attest.cga.serialize_statement`
    and sign with :func:`roam.attest.cga.cosign_sign_statement`.
    """
    subject_name = _resource_uri(change_evidence)
    subject = {
        "name": subject_name,
        "digest": _subject_digest(change_evidence),
    }
    predicate = build_vsa_predicate(
        change_evidence,
        verifier_id=verifier_id,
        policy_uri=policy_uri,
    )
    return {
        "_type": STATEMENT_TYPE,
        "predicateType": SLSA_VSA_PREDICATE_TYPE,
        "subject": [subject],
        "predicate": predicate,
    }


# ---------------------------------------------------------------------------
# Run-ledger HMAC root attestation
# ---------------------------------------------------------------------------


def build_run_ledger_root_predicate(
    *,
    run_id: str,
    final_signature: str,
    event_count: int,
    agent: str | None = None,
    started_at: str | None = None,
    ended_at: str | None = None,
    status: str | None = None,
    repo_id: str | None = None,
) -> dict[str, Any]:
    """Build the predicate body for a run-ledger root attestation.

    The HMAC ``final_signature`` is the rolling-chain tip computed by
    :mod:`roam.runs.signing`: any mutation of any past event in the
    run breaks the chain at that point AND every subsequent
    signature, so a verifier that re-derives the chain from the
    stored ``events.jsonl`` and compares to this attestation gets
    tamper-evidence without needing roam's HMAC secret.

    Why a separate predicate type? The SLSA VSA covers a verdict
    over a code change. The run-ledger root covers the integrity of
    the operational record (HMAC chain). They're orthogonal; mixing
    them into one predicate would prevent independent
    publication / verification.
    """
    predicate: dict[str, Any] = {
        "schema_version": "1",
        "run_id": run_id,
        "final_signature": final_signature,
        "event_count": int(event_count),
        "signature_algorithm": "hmac-sha256",
    }
    if agent:
        predicate["agent"] = agent
    if started_at:
        predicate["started_at"] = started_at
    if ended_at:
        predicate["ended_at"] = ended_at
    if status:
        predicate["status"] = status
    if repo_id:
        predicate["repo_id"] = repo_id
    return predicate


def build_run_ledger_root_statement(
    repo_root: Path,
    run_id: str,
) -> dict[str, Any] | None:
    """Build an in-toto v1 Statement attesting to a run's HMAC root.

    Reads the run's ``meta.json`` (which carries ``final_signature``
    + ``event_count`` after :func:`roam.runs.end_run`). Returns
    ``None`` when the run is unknown OR the chain is unsigned
    (e.g. ledger key missing).

    The Statement's subject names the run; the digest is the
    final_signature itself (sha256 hex - it's an HMAC-SHA256 output,
    so the bytes are sha256-shaped even though the algorithm
    differs).
    """
    from roam.runs.ledger import read_run_meta

    meta = read_run_meta(repo_root, run_id)
    if meta is None:
        return None
    final_sig = getattr(meta, "final_signature", None)
    if not final_sig:
        # Chain unsigned (no ledger key or empty events). Refuse to
        # emit a fake attestation - return None so the caller can
        # surface a "ledger not signed" error to the agent.
        return None
    event_count = int(getattr(meta, "event_count", 0) or 0)

    predicate = build_run_ledger_root_predicate(
        run_id=run_id,
        final_signature=final_sig,
        event_count=event_count,
        agent=getattr(meta, "agent", None),
        started_at=getattr(meta, "started_at", None),
        ended_at=getattr(meta, "ended_at", None),
        status=getattr(meta, "status", None),
    )
    subject = {
        "name": f"urn:roam:run:{run_id}",
        "digest": {"sha256": final_sig},
    }
    return {
        "_type": STATEMENT_TYPE,
        "predicateType": RUN_LEDGER_ROOT_PREDICATE_TYPE,
        "subject": [subject],
        "predicate": predicate,
    }
