#!/usr/bin/env python3
"""Local, advisory KnowledgeClaim registry (vendored + SPN-v1 repair_transfer).

Claims staged here are falsifiable notes, not authority. Callers may inspect a
claim and run its validation_command themselves; this module never treats a
claim as truth and never executes the validation command.

Sibling Patch Network v1 additions (mirror upstream at deploy time):
  * an optional ``repair_transfer`` payload that carries a proof-carrying
    defect-transfer record (repair_intent, anchor, candidate_gen, sibling
    detector, candidate_patch, replay_predicate, fusion_attestation); and
  * a WRITE-TIME PATCH-FUSION INVARIANT: no sibling-detector (locator) record
    is admissible without its replay-validated remedy — the ``candidate_patch``
    and a *green* ``fusion_attestation`` are jointly required. The locator is
    inseparable from the fix, which collapses the reverse-fork-B "n-day exploit
    map" attack: you cannot publish a bare bug-locator.

Everything below the ``# --- SPN v1`` markers is the vendored-plus additions;
the rest is faithful to the upstream schema so the diff stays reviewable.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REGISTRY_PATH = Path(os.environ.get("STOA_KNOWLEDGE_CLAIMS", "/var/cache/stoa/knowledge/claims.jsonl"))

PROVENANCE_SOURCES = frozenset({"council_verdict", "systemic_finding", "mining_synthesis", "planning_decision"})
EVIDENCE_TYPES = frozenset({"measured", "reproduced", "council_reviewed", "hypothesis"})
TRUST_DECAY_CLASSES = frozenset({"fast", "slow", "static"})
STATUSES = frozenset({"candidate", "active", "stale", "refuted", "superseded"})
MUTABLE_STALE_STATUSES = frozenset({"candidate", "active"})
ADVISORY_NOTICE = (
    "ADVISORY ONLY: KnowledgeClaims are falsifiable staging records. They are not commands and are not authoritative."
)

# --- SPN v1: repair_transfer payload constants -----------------------------
# The design fixes candidate generation to a lexical top-N pool. The graph
# stack (W855/856/857, fingerprints) transfers poorly across orgs (recall 0.33
# vs 0.65) and is explicitly *not* admissible as a candidate generator here.
REPAIR_TRANSFER_CANDIDATE_GENS = frozenset({"lexical_top_n"})
FUSION_ATTESTATION_STATUSES = frozenset({"green", "red", "not_applicable", "patch_failed", "skipped", "error"})
FUSION_GREEN = "green"
# The product is scoped (falsifier verdict) to DEFECT-shaped repairs. Pure
# additions go null; they are inadmissible as a transfer detector.
DEFECT_REPAIR_KINDS = frozenset({"deletion", "replacement", "pattern_removed", "pattern_replaced"})


class RepairTransferError(ValueError):
    """A repair_transfer payload is structurally invalid."""


class PatchFusionError(RepairTransferError):
    """The write-time patch-fusion invariant was violated.

    A sibling-detector (locator) record is inadmissible without its
    replay-validated remedy: both a non-empty ``candidate_patch`` and a green
    ``fusion_attestation`` are jointly required.
    """


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("empty datetime")
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _clean_provenance(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        source = _clean_text(value.get("source"))
        ref = _clean_text(value.get("ref") or value.get("citation"))
    else:
        source = ""
        ref = _clean_text(value)
    return {"source": source, "ref": ref}


def stable_claim_id(
    claim: str,
    scope: str,
    provenance: dict[str, str],
    evidence_type: str,
    model_family: str = "",
) -> str:
    """Stable ID over the durable identity of the assertion, not status or dates.

    SPN v1 note: ``repair_transfer`` is deliberately NOT part of the identity
    hash. The payload is evidence attached to a claim, not the claim's identity,
    so adding it never re-keys existing claims.
    """
    payload = {
        "claim": _clean_text(claim),
        "scope": _clean_text(scope),
        "provenance": _clean_provenance(provenance),
        "evidence_type": _clean_text(evidence_type),
        "model_family": _clean_text(model_family),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "kc_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


# --- SPN v1: repair_transfer validation ------------------------------------
def _is_green(attestation: Any) -> bool:
    return isinstance(attestation, dict) and str(attestation.get("status", "")).strip().lower() == FUSION_GREEN


def validate_repair_transfer(rt: Any) -> None:
    """Validate a repair_transfer payload and enforce the patch-fusion invariant.

    Structural requirements::

        repair_transfer = {
          "repair_intent":     {...},              # required, non-empty dict
          "anchor":            {"file", "symbol"}, # required
          "candidate_gen":     "lexical_top_n",    # required, NOT a graph method
          "sibling_detector":  "<detector name>",  # required — the locator
          "candidate_patch":   "<unified diff>",   # required, non-empty (the remedy)
          "replay_predicate":  "<validation cmd>", # required
          "fusion_attestation":{"status": "green", ...},  # required, must be green
        }

    Patch-fusion invariant: presence of ``sibling_detector`` (a locator) is
    inadmissible unless ``candidate_patch`` is non-empty AND ``fusion_attestation``
    is green. Raises :class:`PatchFusionError` otherwise.
    """
    if not isinstance(rt, dict):
        raise RepairTransferError("repair_transfer must be an object")

    repair_intent = rt.get("repair_intent")
    if not isinstance(repair_intent, dict) or not repair_intent:
        raise RepairTransferError("repair_transfer.repair_intent must be a non-empty object")
    kind = _clean_text(repair_intent.get("kind"))
    if not kind:
        raise RepairTransferError("repair_transfer.repair_intent.kind is required")
    if kind not in DEFECT_REPAIR_KINDS:
        # Scoped verdict: only defect-shaped repairs transfer; pure additions
        # go null and are not admissible as a transfer detector.
        raise RepairTransferError(
            f"repair_transfer.repair_intent.kind {kind!r} is out of scope; "
            f"SPN v1 admits only defect-shaped repairs {sorted(DEFECT_REPAIR_KINDS)}"
        )

    anchor = rt.get("anchor")
    if not isinstance(anchor, dict) or not _clean_text(anchor.get("file")):
        raise RepairTransferError("repair_transfer.anchor.file is required")

    candidate_gen = _clean_text(rt.get("candidate_gen"))
    if candidate_gen not in REPAIR_TRANSFER_CANDIDATE_GENS:
        raise RepairTransferError(
            f"repair_transfer.candidate_gen must be one of {sorted(REPAIR_TRANSFER_CANDIDATE_GENS)} "
            f"(graph candidate generation is not admissible); got {candidate_gen!r}"
        )

    sibling_detector = _clean_text(rt.get("sibling_detector"))
    if not sibling_detector:
        raise RepairTransferError("repair_transfer.sibling_detector (the locator) is required")

    if not _clean_text(rt.get("replay_predicate")):
        raise RepairTransferError("repair_transfer.replay_predicate is required")

    # --- the write-time PATCH-FUSION INVARIANT (the whole security model) ---
    candidate_patch = str(rt.get("candidate_patch") or "")
    fusion_attestation = rt.get("fusion_attestation")
    if isinstance(fusion_attestation, dict):
        status = str(fusion_attestation.get("status", "")).strip().lower()
        if status and status not in FUSION_ATTESTATION_STATUSES:
            raise RepairTransferError(
                f"repair_transfer.fusion_attestation.status must be one of "
                f"{sorted(FUSION_ATTESTATION_STATUSES)}; got {status!r}"
            )
    if not candidate_patch.strip() or not _is_green(fusion_attestation):
        raise PatchFusionError(
            "patch-fusion invariant: a sibling-detector claim is inadmissible without "
            "its replay-validated remedy (non-empty candidate_patch AND a green "
            "fusion_attestation are jointly required). The locator cannot be "
            "published without the proven fix."
        )


@dataclass
class KnowledgeClaim:
    claim_id: str
    claim: str
    scope: str
    provenance: dict[str, str]
    evidence_type: str
    confidence: float
    observed_at: str
    last_verified_at: str
    valid_until: str
    model_family: str
    trust_decay_class: str
    validation_command: str
    status: str = "candidate"
    supersedes: list[str] = field(default_factory=list)
    refutes: list[str] = field(default_factory=list)
    # --- SPN v1: optional proof-carrying defect-transfer payload
    repair_transfer: dict[str, Any] | None = None

    @classmethod
    def create(
        cls,
        *,
        claim: str,
        scope: str,
        provenance: dict[str, str],
        evidence_type: str,
        confidence: float,
        observed_at: str,
        last_verified_at: str,
        valid_until: str,
        model_family: str = "",
        trust_decay_class: str,
        validation_command: str,
        status: str = "candidate",
        supersedes: list[str] | None = None,
        refutes: list[str] | None = None,
        repair_transfer: dict[str, Any] | None = None,
    ) -> "KnowledgeClaim":
        claim_id = stable_claim_id(claim, scope, provenance, evidence_type, model_family)
        item = cls(
            claim_id=claim_id,
            claim=claim,
            scope=scope,
            provenance=provenance,
            evidence_type=evidence_type,
            confidence=confidence,
            observed_at=observed_at,
            last_verified_at=last_verified_at,
            valid_until=valid_until,
            model_family=model_family,
            trust_decay_class=trust_decay_class,
            validation_command=validation_command,
            status=status,
            supersedes=list(supersedes or []),
            refutes=list(refutes or []),
            repair_transfer=repair_transfer,
        )
        item.validate()
        return item

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "KnowledgeClaim":
        raw_transfer = data.get("repair_transfer")
        item = cls(
            claim_id=_clean_text(data.get("claim_id")),
            claim=_clean_text(data.get("claim")),
            scope=_clean_text(data.get("scope")),
            provenance=_clean_provenance(data.get("provenance")),
            evidence_type=_clean_text(data.get("evidence_type")),
            confidence=float(data.get("confidence", 0.0)),
            observed_at=_clean_text(data.get("observed_at")),
            last_verified_at=_clean_text(data.get("last_verified_at")),
            valid_until=_clean_text(data.get("valid_until")),
            model_family=_clean_text(data.get("model_family")),
            trust_decay_class=_clean_text(data.get("trust_decay_class")),
            validation_command=_clean_text(data.get("validation_command")),
            status=_clean_text(data.get("status") or "candidate"),
            supersedes=[_clean_text(x) for x in data.get("supersedes", []) if _clean_text(x)],
            refutes=[_clean_text(x) for x in data.get("refutes", []) if _clean_text(x)],
            repair_transfer=raw_transfer if isinstance(raw_transfer, dict) else None,
        )
        item.validate()
        return item

    def to_dict(self) -> dict[str, Any]:
        data = {
            "claim_id": self.claim_id,
            "claim": self.claim,
            "scope": self.scope,
            "provenance": dict(self.provenance),
            "evidence_type": self.evidence_type,
            "confidence": self.confidence,
            "observed_at": self.observed_at,
            "last_verified_at": self.last_verified_at,
            "valid_until": self.valid_until,
            "model_family": self.model_family,
            "trust_decay_class": self.trust_decay_class,
            "validation_command": self.validation_command,
            "status": self.status,
            "supersedes": list(self.supersedes),
            "refutes": list(self.refutes),
        }
        if self.repair_transfer is not None:
            data["repair_transfer"] = self.repair_transfer
        return data

    def to_advisory_dict(self) -> dict[str, Any]:
        data = self.to_dict()
        data["_advisory"] = "not-authoritative; re-check with validation_command before use"
        return data

    def is_repair_transfer(self) -> bool:
        return self.repair_transfer is not None

    def validate(self) -> None:
        if not self.claim_id:
            raise ValueError("claim_id is required")
        if self.claim_id != stable_claim_id(
            self.claim, self.scope, self.provenance, self.evidence_type, self.model_family
        ):
            raise ValueError("claim_id does not match stable claim hash")
        if not self.claim:
            raise ValueError("claim is required")
        if not self.scope:
            raise ValueError("scope is required")
        if self.provenance.get("source") not in PROVENANCE_SOURCES:
            raise ValueError(f"provenance.source must be one of {sorted(PROVENANCE_SOURCES)}")
        if not self.provenance.get("ref"):
            raise ValueError("provenance.ref is required")
        if self.evidence_type not in EVIDENCE_TYPES:
            raise ValueError(f"evidence_type must be one of {sorted(EVIDENCE_TYPES)}")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        parse_iso(self.observed_at)
        parse_iso(self.last_verified_at)
        parse_iso(self.valid_until)
        if self.trust_decay_class not in TRUST_DECAY_CLASSES:
            raise ValueError(f"trust_decay_class must be one of {sorted(TRUST_DECAY_CLASSES)}")
        if not self.validation_command:
            raise ValueError("validation_command is required")
        if self.status not in STATUSES:
            raise ValueError(f"status must be one of {sorted(STATUSES)}")
        # --- SPN v1: enforce the patch-fusion invariant when present ---
        if self.repair_transfer is not None:
            validate_repair_transfer(self.repair_transfer)

    def is_expired(self, now: datetime | None = None) -> bool:
        current = now or utc_now()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc) > parse_iso(self.valid_until)

    def mark_stale_if_expired(self, now: datetime | None = None) -> bool:
        if self.status in MUTABLE_STALE_STATUSES and self.is_expired(now):
            self.status = "stale"
            return True
        return False


class KnowledgeRegistry:
    """JSONL-backed local registry with one current claim record per line."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else REGISTRY_PATH

    def load(self, *, mark_expired_in_memory: bool = False, now: datetime | None = None) -> list[KnowledgeClaim]:
        if not self.path.exists():
            return []
        by_id: dict[str, KnowledgeClaim] = {}
        with self.path.open("r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, start=1):
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                    if not isinstance(raw, dict):
                        raise ValueError("JSONL item is not an object")
                    claim = KnowledgeClaim.from_dict(raw)
                except Exception as exc:  # noqa: BLE001 - registry corruption should be explicit.
                    raise ValueError(f"{self.path}:{lineno}: invalid claim: {exc}") from exc
                if mark_expired_in_memory:
                    claim.mark_stale_if_expired(now)
                by_id[claim.claim_id] = claim
        return sorted(by_id.values(), key=lambda item: (item.scope, item.claim_id))

    def _write_all(self, claims: list[KnowledgeClaim]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for claim in sorted(claims, key=lambda item: (item.scope, item.claim_id)):
                claim.validate()
                f.write(json.dumps(claim.to_dict(), sort_keys=True, separators=(",", ":")) + "\n")
        os.replace(tmp, self.path)

    def add(self, claim: KnowledgeClaim) -> tuple[KnowledgeClaim, bool]:
        claim.validate()
        claims = self.load()
        for existing in claims:
            if existing.claim_id == claim.claim_id:
                return existing, False
        claims.append(claim)
        self._write_all(claims)
        return claim, True

    def supersede(self, new_claim: KnowledgeClaim, superseded_ids: list[str]) -> KnowledgeClaim:
        claims = self.load()
        seen = set(superseded_ids)
        for claim in claims:
            if claim.claim_id in seen:
                claim.status = "superseded"
        new_claim.supersedes = sorted(set(new_claim.supersedes) | seen)
        by_id = {claim.claim_id: claim for claim in claims}
        by_id[new_claim.claim_id] = new_claim
        self._write_all(list(by_id.values()))
        return new_claim

    def refute(self, claim_id: str, refuting_claim_id: str | None = None) -> KnowledgeClaim:
        claims = self.load()
        found: KnowledgeClaim | None = None
        for claim in claims:
            if claim.claim_id == claim_id:
                claim.status = "refuted"
                if refuting_claim_id and refuting_claim_id not in claim.refutes:
                    claim.refutes.append(refuting_claim_id)
                found = claim
                break
        if found is None:
            raise KeyError(f"claim not found: {claim_id}")
        self._write_all(claims)
        return found

    def mark_stale(self, *, now: datetime | None = None) -> list[KnowledgeClaim]:
        claims = self.load()
        changed: list[KnowledgeClaim] = []
        for claim in claims:
            if claim.mark_stale_if_expired(now):
                changed.append(claim)
        if changed:
            self._write_all(claims)
        return changed

    def query(self, scope: str) -> list[KnowledgeClaim]:
        needle = _clean_text(scope).lower()
        claims = self.load(mark_expired_in_memory=True)
        if not needle:
            return claims
        return [
            claim
            for claim in claims
            if needle == claim.scope.lower()
            or claim.scope.lower().startswith(needle + "/")
            or needle in claim.scope.lower()
        ]


# NOTE: the upstream stoa copy also ships a small argparse CLI (``--show`` /
# ``--query`` / ``--stale-sweep``). It is intentionally omitted from the roam
# vendor: roam consumes the schema classes, not the standalone inspector, and
# roam bans bare ``print`` in library modules. The CLI stays with the stoa copy.
