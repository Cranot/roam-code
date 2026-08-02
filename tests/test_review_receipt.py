"""W1443 — the review-receipt verifier refuses every forgery shape.

The receipt is written by the agent being judged, so every test here is a
forgery attempt: the question is never "does an honest receipt pass" (one
test covers that) but "can an agent that skipped, botched, or recycled a
review still reach ``accepted``".
"""

from __future__ import annotations

import json

import pytest

from roam.review_receipt import (
    REVIEW_FAMILIES,
    REVIEW_STATUSES,
    canonical_artifact_sha256,
    verify_receipt,
)

PLAN_TEXT = "1. touch ledger.py\n2. add the write lock\n3. run the ledger tests\n"


def _sha(text: str = PLAN_TEXT) -> str:
    return canonical_artifact_sha256(text)


def _receipt(**overrides) -> dict:
    base = {
        "schema": "roam-review-receipt-v1",
        "phase": "1b_plan_critique",
        "criteria_template": "plan-critique-v1",
        "builder_family": "claude",
        "reviewer_family": "openai",
        "artifact_sha256": _sha(),
        "decision": "accept",
        "findings": [],
        "reviewed_at": "2026-08-02T12:00:00Z",
    }
    base.update(overrides)
    return base


def _write(tmp_path, receipt: dict | str, name: str = "receipt.json"):
    path = tmp_path / name
    path.write_text(
        receipt if isinstance(receipt, str) else json.dumps(receipt),
        encoding="utf-8",
    )
    return path


def _verify(tmp_path, path, *, phase="1b_plan_critique", artifact=PLAN_TEXT):
    return verify_receipt(
        path,
        expected_phase=phase,
        artifact_bytes=artifact,
        repo_root=tmp_path,
    )


# ---------------------------------------------------------------------------
# the one honest path
# ---------------------------------------------------------------------------


def test_honest_receipt_is_accepted(tmp_path):
    res = _verify(tmp_path, _write(tmp_path, _receipt()))
    assert res["status"] == "declared_accepted"
    assert res["claims"]["reviewer_family"] == "openai"
    assert res["derived"]["blocking_findings_count"] == 0


def test_canonical_hash_survives_line_ending_differences(tmp_path):
    """An honest review on Windows must not read as stale on Linux."""
    assert canonical_artifact_sha256(PLAN_TEXT) == canonical_artifact_sha256(PLAN_TEXT.replace("\n", "\r\n"))
    # ...but an interior content change is a real change and must not match
    assert canonical_artifact_sha256(PLAN_TEXT) != canonical_artifact_sha256(
        PLAN_TEXT.replace("write lock", "write lock and fsync")
    )


# ---------------------------------------------------------------------------
# forgeries: skipping the review entirely
# ---------------------------------------------------------------------------


def test_absent_receipt_is_missing_not_green(tmp_path):
    res = _verify(tmp_path, tmp_path / "never-written.json")
    assert res["status"] == "receipt_missing"


def test_directory_in_place_of_receipt_is_refused(tmp_path):
    (tmp_path / "receipt.json").mkdir()
    assert _verify(tmp_path, tmp_path / "receipt.json")["status"] == "receipt_malformed"


def test_receipt_outside_the_repo_is_refused(tmp_path):
    outside = tmp_path.parent / "elsewhere.json"
    outside.write_text(json.dumps(_receipt()), encoding="utf-8")
    res = verify_receipt(
        outside,
        expected_phase="1b_plan_critique",
        artifact_bytes=PLAN_TEXT,
        repo_root=tmp_path,
    )
    assert res["status"] == "receipt_malformed"
    assert "escapes the repository" in res["reason"]


def test_oversized_receipt_is_refused(tmp_path):
    fat = _receipt()
    fat["notes"] = "x" * (1 << 21)
    assert _verify(tmp_path, _write(tmp_path, fat))["status"] == "receipt_malformed"


def test_non_json_and_non_object_receipts_are_refused(tmp_path):
    assert _verify(tmp_path, _write(tmp_path, "not json at all"))["status"] == "receipt_malformed"
    assert _verify(tmp_path, _write(tmp_path, "[1, 2, 3]"))["status"] == "receipt_malformed"


@pytest.mark.parametrize("field", ["phase", "decision", "artifact_sha256", "builder_family"])
def test_missing_required_field_is_malformed(tmp_path, field):
    receipt = _receipt()
    del receipt[field]
    res = _verify(tmp_path, _write(tmp_path, receipt))
    assert res["status"] == "receipt_malformed"
    assert field in res["reason"]


def test_unknown_field_is_refused_not_ignored(tmp_path):
    """A receipt must not smuggle in a property roam does not honour."""
    res = _verify(tmp_path, _write(tmp_path, _receipt(verified_by_provider=True)))
    assert res["status"] == "receipt_malformed"
    assert "verified_by_provider" in res["reason"]


# ---------------------------------------------------------------------------
# forgeries: identity (A3 — the one that went green in the emulation walk)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bogus", ["unknown", "", "Claude", "gpt", None, 7])
def test_unresolved_family_fails_closed(tmp_path, bogus):
    """'unknown' is literally disjoint from 'openai' — it must NOT pass."""
    res = _verify(tmp_path, _write(tmp_path, _receipt(builder_family=bogus)))
    assert res["status"] == "family_unresolved"
    assert "builder_family" in res["reason"]


def test_same_family_review_blocks(tmp_path):
    res = _verify(tmp_path, _write(tmp_path, _receipt(reviewer_family="claude")))
    assert res["status"] == "same_family"


def test_identity_is_checked_before_the_decision_is_honoured(tmp_path):
    """An accept decision cannot rescue an unresolvable identity."""
    res = _verify(
        tmp_path,
        _write(tmp_path, _receipt(reviewer_family="mystery", decision="accept")),
    )
    assert res["status"] == "family_unresolved"


# ---------------------------------------------------------------------------
# forgeries: recycling a real review onto different bytes
# ---------------------------------------------------------------------------


def test_receipt_for_other_bytes_is_stale(tmp_path):
    res = _verify(tmp_path, _write(tmp_path, _receipt()), artifact="a different plan entirely")
    assert res["status"] == "artifact_stale"


def test_receipt_for_the_wrong_phase_is_refused(tmp_path):
    res = _verify(tmp_path, _write(tmp_path, _receipt()), phase="4b_done_verdict")
    assert res["status"] == "wrong_phase"


def test_phase_and_criteria_template_must_agree(tmp_path):
    """A 4b receipt carrying the 1b criteria template is refused."""
    receipt = _receipt(phase="4b_done_verdict", criteria_template="plan-critique-v1")
    res = _verify(tmp_path, _write(tmp_path, receipt), phase="4b_done_verdict")
    assert res["status"] == "receipt_malformed"
    assert "criteria_template" in res["reason"]


# ---------------------------------------------------------------------------
# forgeries: a review that happened but went badly
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("decision", ["revise", "reject"])
def test_negative_decision_blocks(tmp_path, decision):
    res = _verify(tmp_path, _write(tmp_path, _receipt(decision=decision)))
    assert res["status"] == "rejected"


def test_blocking_findings_override_an_accept(tmp_path):
    """Occurrence is not success: 'accept' with blockers recorded is not green."""
    receipt = _receipt(
        decision="accept",
        findings=[{"title": "race", "severity": "critical"}, {"title": "leak", "severity": "high"}],
    )
    res = _verify(tmp_path, _write(tmp_path, receipt))
    assert res["status"] == "rejected"
    assert res["derived"]["blocking_findings_count"] == 2


def test_errored_review_is_not_a_pass(tmp_path):
    assert _verify(tmp_path, _write(tmp_path, _receipt(decision="error")))["status"] == ("review_error")


@pytest.mark.parametrize("bad", ["not-a-list", {"a": 1}, None, 3])
def test_malformed_findings_are_refused(tmp_path, bad):
    res = _verify(tmp_path, _write(tmp_path, _receipt(findings=bad)))
    assert res["status"] == "receipt_malformed"


def test_unknown_finding_severity_is_refused(tmp_path):
    """An unrecognised severity must not silently count as non-blocking."""
    receipt = _receipt(findings=[{"title": "x", "severity": "spicy"}])
    res = _verify(tmp_path, _write(tmp_path, receipt))
    assert res["status"] == "receipt_malformed"
    assert "severity" in res["reason"]


# ---------------------------------------------------------------------------
# vocabulary discipline
# ---------------------------------------------------------------------------


def test_every_returned_status_is_in_the_closed_vocabulary(tmp_path):
    cases = [
        _receipt(),
        _receipt(decision="reject"),
        _receipt(builder_family="unknown"),
        _receipt(reviewer_family="claude"),
        _receipt(decision="error"),
        _receipt(findings=[{"title": "x", "severity": "blocker"}]),
    ]
    seen = {_verify(tmp_path, _write(tmp_path, c))["status"] for c in cases}
    assert seen <= set(REVIEW_STATUSES)
    assert _verify(tmp_path, tmp_path / "absent.json")["status"] in REVIEW_STATUSES


def test_family_vocabulary_matches_calibration(tmp_path):
    """The closed set must track the profile literals, not drift from them."""
    import typing

    from roam.plan import calibration

    # ``from __future__ import annotations`` stores annotations as strings,
    # so resolve them before reading the Literal members.
    hints = typing.get_type_hints(calibration.CalibrationProfile)
    literals = set(typing.get_args(hints["family"]))
    assert literals, "could not resolve the family Literal — drift guard is blind"
    assert literals == set(REVIEW_FAMILIES)


def test_unknown_phase_is_a_programming_error(tmp_path):
    with pytest.raises(ValueError):
        verify_receipt(
            _write(tmp_path, _receipt()),
            expected_phase="9z_not_a_phase",
            artifact_bytes=PLAN_TEXT,
            repo_root=tmp_path,
        )


# ---------------------------------------------------------------------------
# folded from the cross-family critique of this design
# ---------------------------------------------------------------------------


def test_duplicate_json_keys_are_refused(tmp_path):
    """A stock decoder keeps the LAST value, so a reject/accept pair would
    read as accept while the file's first line says reject."""
    raw = json.dumps(_receipt())
    forged = raw.replace('"decision": "accept"', '"decision": "reject", "decision": "accept"', 1)
    res = _verify(tmp_path, _write(tmp_path, forged))
    assert res["status"] == "receipt_malformed"
    assert "duplicate key" in res["reason"]


def test_receipt_cannot_state_its_own_blocker_count(tmp_path):
    """The count is DERIVED from findings; stating it is an unknown field."""
    res = _verify(tmp_path, _write(tmp_path, _receipt(blocking_findings_count=0)))
    assert res["status"] == "receipt_malformed"
    assert "blocking_findings_count" in res["reason"]


def test_verifier_derives_the_digest_itself(tmp_path):
    """The caller passes BYTES, never a digest: if it passed the digest,
    both sides of the comparison could come from the party being judged."""
    import inspect

    from roam.review_receipt import verify_receipt as fn

    params = inspect.signature(fn).parameters
    assert "artifact_bytes" in params
    assert "artifact_sha256" not in params
    # and the derived digest is reported, so a caller can bind to it
    res = _verify(tmp_path, _write(tmp_path, _receipt()))
    assert res["derived"]["artifact_sha256"] == canonical_artifact_sha256(PLAN_TEXT)


def test_claims_and_derived_are_separated(tmp_path):
    """Agent-written values never share a key with verifier-computed ones."""
    res = _verify(tmp_path, _write(tmp_path, _receipt()))
    assert set(res) == {"status", "reason", "claims", "derived"}
    assert "reviewer_family" in res["claims"]
    assert "reviewer_family" not in res["derived"]


def test_success_status_names_its_own_limit(tmp_path):
    """Occurrence is not provable here; the status must not say 'verified'."""
    res = _verify(tmp_path, _write(tmp_path, _receipt()))
    assert res["status"] == "declared_accepted"
    assert "verified" not in res["status"]
