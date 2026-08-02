"""Verification of cross-family review receipts (orchestration graph, 1b/4b).

The compile envelope's ``orchestration_contract`` (see
``roam.plan.compiler``) tells an agent that its plan must survive a
critique by a DIFFERENT model family before implementation, and that
"done" needs a same-shaped verdict on the finished diff. This module
decides whether those obligations were actually met, so
:mod:`roam.verdict` can block when they were not.

HONEST SCOPE — declared, not attested. roam-code never calls a model, and
no provider exposes a signed "this family produced this text" attestation.
So a receipt's ``builder_family`` / ``reviewer_family`` are CLAIMS, and so
is the fact that a review happened at all. An agent determined to forge
can write a well-formed receipt for a review it never ran.

That is why the success status is named ``declared_accepted`` and not
``accepted``: what this module proves is *bounded* — the declaration is
well-formed, drawn from a closed vocabulary, internally consistent, and
BOUND BY HASH to the exact bytes under judgement. That is enough to catch
every ACCIDENTAL failure (a stale review, a recycled receipt, a negative
outcome, an unresolved identity, the wrong phase) and to make a forgery a
deliberate, recorded, auditable act. It is not proof of occurrence, and no
surface may describe it as verified diversity.

The trust rule, stated once: every field an agent writes is an untrusted
claim. Where a property is security-relevant this module RECOMPUTES it
from bytes it opens itself — notably the artifact digest, which is why
:func:`verify_receipt` takes artifact BYTES, never a caller-supplied
digest (a caller-supplied digest would let the two values being compared
both come from the party being judged).
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any

RECEIPT_SCHEMA = "roam-review-receipt-v1"

# Phases an obligation can name. Mirrors the criteria templates the
# compile envelope advertises in ``orchestration_contract``.
REVIEW_PHASES: tuple[str, ...] = ("1b_plan_critique", "4b_done_verdict")

PHASE_CRITERIA_TEMPLATE: dict[str, str] = {
    "1b_plan_critique": "plan-critique-v1",
    "4b_done_verdict": "done-verdict-v1",
}

# Closed family vocabulary, mirroring ``calibration.CalibrationProfile``'s
# ``family`` literal. Closed on purpose: an open string field lets
# ``builder_family="unknown"`` read as "disjoint from openai" and sail
# through the cross-family check, which is the exact hole a forgery walk
# found. Anything outside this set is UNRESOLVED, which is non-green.
REVIEW_FAMILIES: frozenset[str] = frozenset({"claude", "openai", "google", "open-weight"})

REVIEW_DECISIONS: frozenset[str] = frozenset({"accept", "revise", "reject", "error"})

# Closed status vocabulary. Every non-``accepted`` value maps to exactly
# one verdict blocker (see ``roam.verdict``), so no failure mode can
# reach a caller as an unclassified string.
REVIEW_STATUSES: tuple[str, ...] = (
    # "declared_", not "accepted": occurrence is not provable here (see
    # the module docstring). Naming the limit in the value itself stops a
    # downstream surface from quietly upgrading it to "verified".
    "declared_accepted",
    "receipt_missing",
    "receipt_malformed",
    "artifact_stale",
    "wrong_phase",
    "same_family",
    "family_unresolved",
    "rejected",
    "review_error",
)

# Severities that make a finding blocking. The count is DERIVED from the
# findings list; a receipt cannot state its own blocker count, because
# "3 critical findings, blocking_findings_count: 0, decision: accept" is
# otherwise a one-field forgery.
BLOCKING_SEVERITIES: frozenset[str] = frozenset({"blocker", "critical", "high"})
FINDING_SEVERITIES: frozenset[str] = BLOCKING_SEVERITIES | {"medium", "low", "info"}

# A receipt is untrusted content read from disk: cap it rather than
# reading an arbitrarily large file into memory.
MAX_RECEIPT_BYTES = 1 << 20  # 1 MiB

_REQUIRED_FIELDS = (
    "schema",
    "phase",
    "criteria_template",
    "builder_family",
    "reviewer_family",
    "artifact_sha256",
    "decision",
    "findings",
)

_ALLOWED_FIELDS = frozenset(
    _REQUIRED_FIELDS
    + (
        "reviewed_at",  # provenance only; never authority for freshness
        "reviewer_model",
        "notes",
    )
)


def canonical_artifact_sha256(data: bytes | str) -> str:
    """Hash of an artifact under one canonical form.

    Line endings are normalised (CRLF/CR -> LF) and trailing whitespace at
    end-of-text is stripped before hashing. Without this, an honest review
    of the same plan hashes differently on Windows and Linux and reads as
    ``artifact_stale`` -- a false block that would train users to bypass
    the gate. Nothing else is normalised: interior whitespace changes are
    real content changes and MUST invalidate a receipt.
    """
    if isinstance(data, bytes):
        text = data.decode("utf-8", "replace")
    else:
        text = data
    normalised = text.replace("\r\n", "\n").replace("\r", "\n").rstrip()
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict:
    """``object_pairs_hook`` that refuses duplicate keys.

    A stock JSON decoder keeps the LAST value, so
    ``{"decision": "reject", "decision": "accept"}`` parses to accept while
    a human (or a diff) reading the file sees the rejection first.
    """
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError(f"duplicate key {key!r} in receipt")
        seen[key] = value
    return seen


def _result(
    status: str,
    reason: str,
    claims: dict | None = None,
    derived: dict | None = None,
) -> dict[str, Any]:
    """A verification outcome.

    ``claims`` is what the agent wrote (echoed for diagnosis, never
    authority); ``derived`` is what this module computed. Keeping them in
    separate keys stops a downstream surface from reading an agent-written
    field as though the verifier had established it.
    """
    return {
        "status": status,
        "reason": reason,
        "claims": claims,
        "derived": derived or {},
    }


def _read_receipt_bytes(path: Path, repo_root: Path) -> tuple[bytes | None, dict | None]:
    """Read a receipt file safely; returns ``(data, error_result)``.

    Refuses anything that is not a regular file inside the repository:
    a symlink or device node here would let a receipt path pull bytes
    from somewhere the agent should not be able to reach.
    """
    try:
        resolved = path.resolve()
        root = repo_root.resolve()
    except OSError as exc:
        return None, _result("receipt_missing", f"receipt path unresolvable: {exc}")

    if not (resolved == root or root in resolved.parents):
        return None, _result(
            "receipt_malformed",
            f"receipt path escapes the repository: {resolved}",
        )
    try:
        info = os.lstat(resolved)
    except OSError as exc:
        return None, _result("receipt_missing", f"receipt not readable: {exc}")
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        return None, _result("receipt_malformed", "receipt must be a regular file, not a link or device")
    if info.st_size > MAX_RECEIPT_BYTES:
        return None, _result(
            "receipt_malformed",
            f"receipt exceeds {MAX_RECEIPT_BYTES} bytes ({info.st_size})",
        )
    try:
        return resolved.read_bytes(), None
    except OSError as exc:
        return None, _result("receipt_missing", f"receipt not readable: {exc}")


def _structural_errors(receipt: dict) -> str | None:
    """Schema/shape validation, phase-independent. Reason string or None."""
    if receipt.get("schema") != RECEIPT_SCHEMA:
        return f"schema must be {RECEIPT_SCHEMA!r}, got {receipt.get('schema')!r}"
    missing = [f for f in _REQUIRED_FIELDS if f not in receipt]
    if missing:
        return f"missing required field(s): {', '.join(missing)}"
    # Unknown fields are refused rather than ignored: a receipt carrying
    # e.g. "verified_by_provider": true must never pass as though roam
    # had honoured it. (Schema-version discipline, not a security control:
    # the security comes from never consuming unlisted fields at all.)
    unknown = sorted(set(receipt) - _ALLOWED_FIELDS)
    if unknown:
        return f"unknown field(s) not honoured by this schema: {', '.join(unknown)}"
    if receipt.get("phase") not in PHASE_CRITERIA_TEMPLATE:
        return f"phase must be one of {sorted(PHASE_CRITERIA_TEMPLATE)}, got {receipt.get('phase')!r}"
    expected_template = PHASE_CRITERIA_TEMPLATE[receipt["phase"]]
    if receipt.get("criteria_template") != expected_template:
        return (
            f"criteria_template {receipt.get('criteria_template')!r} does not match "
            f"phase {receipt['phase']!r} (expected {expected_template!r})"
        )
    if receipt.get("decision") not in REVIEW_DECISIONS:
        return f"decision must be one of {sorted(REVIEW_DECISIONS)}, got {receipt.get('decision')!r}"
    sha = receipt.get("artifact_sha256")
    if not isinstance(sha, str) or len(sha) != 64 or set(sha) - set("0123456789abcdef"):
        return "artifact_sha256 must be a 64-char lowercase hex digest"
    findings = receipt.get("findings")
    if not isinstance(findings, list):
        return "findings must be a list (use [] for a clean review)"
    for i, finding in enumerate(findings):
        if not isinstance(finding, dict):
            return f"findings[{i}] must be an object"
        severity = finding.get("severity")
        if severity not in FINDING_SEVERITIES:
            return f"findings[{i}].severity must be one of {sorted(FINDING_SEVERITIES)}, got {severity!r}"
    return None


def _derived_blocking_count(receipt: dict) -> int:
    """Count blocking findings FROM the findings list, never from a field."""
    return sum(1 for f in receipt["findings"] if f.get("severity") in BLOCKING_SEVERITIES)


def verify_receipt(
    receipt_path: str | Path,
    *,
    expected_phase: str,
    artifact_bytes: bytes | str,
    repo_root: str | Path,
) -> dict[str, Any]:
    """Decide whether a review obligation was met, from a receipt on disk.

    ``artifact_bytes`` is the artifact under judgement NOW -- the actual
    plan text or diff, passed as bytes. This module derives the digest
    itself and compares the receipt's claim against it. It deliberately
    does NOT accept a caller-supplied digest: if the caller passed both
    the digest and the receipt, both sides of the comparison could come
    from the party being judged, and the "verifier recomputes it" property
    would be a comment rather than a mechanism.

    Callers must read the artifact ONCE and pass those exact bytes on to
    whatever consumes the verdict; re-reading the path afterwards
    reintroduces a TOCTOU window this function cannot close for them.

    Returns ``{"status", "reason", "claims", "derived"}`` with ``status``
    drawn from :data:`REVIEW_STATUSES`.
    """
    if expected_phase not in PHASE_CRITERIA_TEMPLATE:
        raise ValueError(f"unknown review phase {expected_phase!r}")

    artifact_sha256 = canonical_artifact_sha256(artifact_bytes)
    derived = {"artifact_sha256": artifact_sha256, "expected_phase": expected_phase}

    data, err = _read_receipt_bytes(Path(receipt_path), Path(repo_root))
    if err is not None:
        err["derived"] = derived
        return err
    assert data is not None
    try:
        receipt = json.loads(data.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return _result("receipt_malformed", f"receipt is not valid JSON: {exc}", derived=derived)
    if not isinstance(receipt, dict):
        return _result("receipt_malformed", "receipt must be a JSON object", derived=derived)

    reason = _structural_errors(receipt)
    if reason is not None:
        return _result("receipt_malformed", reason, derived=derived)

    # Phase mismatch is its own status: a receipt that is perfectly valid
    # for 4b, presented for 1b, is a DIFFERENT fault from a malformed one
    # and gets its own blocker rather than being lumped in.
    if receipt["phase"] != expected_phase:
        return _result(
            "wrong_phase",
            f"receipt is for phase {receipt['phase']!r}, expected {expected_phase!r}",
            receipt,
            derived,
        )

    # Identity BEFORE outcome: an unresolved family means we cannot even
    # say whether the review was cross-family, so no decision it carries
    # can be honoured. Fails closed (this is the "unknown is literally
    # disjoint from openai, so it went green" hole).
    builder = receipt.get("builder_family")
    reviewer = receipt.get("reviewer_family")
    unresolved = [
        name
        for name, value in (("builder_family", builder), ("reviewer_family", reviewer))
        if not isinstance(value, str) or value not in REVIEW_FAMILIES
    ]
    if unresolved:
        return _result(
            "family_unresolved",
            f"{', '.join(unresolved)} not in the known family vocabulary "
            f"{sorted(REVIEW_FAMILIES)}; identity must resolve before a review counts",
            receipt,
            derived,
        )
    if builder == reviewer:
        return _result(
            "same_family",
            f"reviewer_family {reviewer!r} equals builder_family: a same-family review "
            "reproduces the builder's blind spot",
            receipt,
            derived,
        )

    # Binding: the review must be OF the artifact under judgement. Both
    # sides are compared against a digest THIS function computed.
    if receipt["artifact_sha256"] != artifact_sha256:
        return _result(
            "artifact_stale",
            "receipt reviews different bytes than the artifact under judgement "
            f"(receipt {receipt['artifact_sha256'][:12]}..., actual {artifact_sha256[:12]}...)",
            receipt,
            derived,
        )

    # Occurrence is not success.
    blocking = _derived_blocking_count(receipt)
    derived = {**derived, "blocking_findings_count": blocking}
    decision = receipt["decision"]
    if decision == "error":
        return _result("review_error", "the review itself errored; no verdict was reached", receipt, derived)
    if decision in ("revise", "reject"):
        return _result("rejected", f"the reviewer returned {decision!r}", receipt, derived)
    if blocking > 0:
        return _result(
            "rejected",
            f"decision was 'accept' but {blocking} blocking finding(s) are recorded "
            "(severity in "
            f"{sorted(BLOCKING_SEVERITIES)}); the findings override the stated decision",
            receipt,
            derived,
        )
    return _result(
        "declared_accepted",
        "review declares accept with no blocking findings, bound to the current artifact",
        receipt,
        derived,
    )
