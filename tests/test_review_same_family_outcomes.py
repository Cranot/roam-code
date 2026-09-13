"""Family coverage warnings must not bypass receipt identity or review outcomes."""

from __future__ import annotations

import json

import pytest

from roam.review_receipt import DIGEST_SCHEME, canonical_artifact_sha256, verify_receipt
from roam.verdict import compute_verdict


@pytest.mark.parametrize("phase", ["1b_plan_critique", "4b_done_verdict"])
@pytest.mark.parametrize("family", ["openai", "claude"])
@pytest.mark.parametrize(
    "variant,expected",
    [
        ("valid", None),
        ("stale", "artifact_stale"),
        ("reject", "rejected"),
        ("revise", "rejected"),
        ("error", "review_error"),
        ("blocking", "rejected"),
    ],
)
def test_family_coverage_never_overrides_binding_or_outcome(tmp_path, phase, family, variant, expected):
    # A real temporary receipt exercises path checks and JSON acquisition;
    # serialized verifier output then reaches the actual required-review consumer.
    artifact = b"the exact proposed change\n"
    receipt = {
        "schema": "roam-review-receipt-v1",
        "phase": phase,
        "criteria_template": "plan-critique-v1" if phase.startswith("1b") else "done-verdict-v1",
        "builder_family": "openai",
        "reviewer_family": family,
        "artifact_sha256": canonical_artifact_sha256(artifact),
        "digest_scheme": DIGEST_SCHEME,
        "decision": "accept",
        "findings": [],
    }
    if variant == "stale":
        receipt["artifact_sha256"] = "0" * 64
    elif variant in {"reject", "revise", "error"}:
        receipt["decision"] = variant
    elif variant == "blocking":
        receipt["findings"] = [{"severity": "high", "message": "A supported blocking finding"}]
    path = tmp_path / "review.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")
    result = verify_receipt(path, expected_phase=phase, artifact_bytes=artifact, repo_root=tmp_path)
    result = json.loads(json.dumps(result))
    other_phase = "4b_done_verdict" if phase.startswith("1b") else "1b_plan_critique"
    verdict = compute_verdict(
        verification_contract={"required": []},
        risk={"level": "medium", "assessment_status": "complete", "tags": []},
        orchestration_contract={"obligations": ["1b", "4b"]},
        review_evidence={phase: result, other_phase: {"status": "declared_accepted"}},
    )
    if expected:
        assert result["status"] == expected
        assert verdict["value"] == "blocked"
    else:
        assert result["status"] == ("same_family" if family == "openai" else "declared_accepted")
        assert result["derived"]["blocking_findings_count"] == 0
        assert verdict["value"] == ("pass_with_warnings" if family == "openai" else "pass")
