"""Tests for G3 verification_contract + closed-enum verdict engine.

Per the Roam Guard pivot decision: these are the two new modules feeding
the AgentChangeProofBundle v1 schema emission.
"""

from __future__ import annotations

import pytest

from roam.verdict import VERDICTS, compute_verdict, verdict_exit_code
from roam.verification_contract import build_verification_contract

# ---- fixtures ----

_SAMPLE_GRAPH = {
    "commands": [
        {"name": "pytest", "kind": "test", "invocation": "pytest tests/"},
        {"name": "ts:test", "kind": "test", "invocation": "npm run ts:test"},
        {"name": "lint", "kind": "lint", "invocation": "ruff check src/"},
        {"name": "build", "kind": "build", "invocation": "make build"},
    ]
}


# ---- verification_contract tests ----


def test_contract_auth_file_requires_tests():
    c = build_verification_contract(
        changed_files=["src/auth/session.py"],
        command_graph=_SAMPLE_GRAPH,
    )
    required_names = {r["command"] for r in c["required"]}
    assert "pytest" in required_names
    reasons = {r["reason"] for r in c["required"]}
    assert "auth_file_changed" in reasons


def test_contract_high_risk_requires_all_test_commands():
    c = build_verification_contract(
        changed_files=["lib/foo.rb"],
        command_graph=_SAMPLE_GRAPH,
        risk={"level": "high", "reasons": ["touches billing"], "paths": ["lib/foo.rb"]},
    )
    required_names = {r["command"] for r in c["required"]}
    # All test commands required under high risk
    assert "pytest" in required_names
    assert "ts:test" in required_names
    assert any(r["reason"] == "high_risk_path" for r in c["required"])


def test_contract_regulated_policy_floor():
    c = build_verification_contract(
        changed_files=["docs/changelog.md"],  # NOT typically test-required
        command_graph=_SAMPLE_GRAPH,
        policy_profile="regulated",
    )
    required_names = {r["command"] for r in c["required"]}
    assert "pytest" in required_names
    reasons = {r["reason"] for r in c["required"]}
    assert "policy_floor" in reasons


def test_contract_skips_lint_when_kind_isnt_test():
    c = build_verification_contract(
        changed_files=["src/random.py"],
        command_graph=_SAMPLE_GRAPH,
    )
    skipped_names = {r["command"] for r in c["skipped"]}
    # build + lint don't get required just because there's a Python file change
    assert "build" in skipped_names


def test_contract_empty_changes_skips_all():
    c = build_verification_contract(
        changed_files=[],
        command_graph=_SAMPLE_GRAPH,
    )
    assert c["required"] == []
    assert len(c["skipped"]) == len(_SAMPLE_GRAPH["commands"])


def test_contract_includes_meta_block():
    c = build_verification_contract(
        changed_files=["src/auth/session.py"],
        command_graph=_SAMPLE_GRAPH,
        risk={"level": "high", "paths": ["src/auth/session.py"]},
        mode="autonomous_pr",
        policy_profile="regulated",
    )
    assert c["_meta"]["mode"] == "autonomous_pr"
    assert c["_meta"]["policy_profile"] == "regulated"
    assert "src/auth/session.py" in c["_meta"]["high_risk_path_hits"]


# ---- verdict engine tests ----


def test_verdict_pass_when_required_ran_and_passed():
    contract = {
        "required": [{"command": "pytest", "kind": "test", "reason": "auth_file_changed"}],
        "skipped": [],
    }
    v = compute_verdict(
        verification_contract=contract,
        executed_checks=[{"command": "pytest", "status": "pass"}],
    )
    assert v["value"] == "pass"
    assert any(r["code"] == "all_required_passed" for r in v["reasons"])


def test_verdict_blocked_when_required_not_run():
    contract = {
        "required": [{"command": "pytest", "kind": "test", "reason": "auth_file_changed"}],
        "skipped": [],
    }
    v = compute_verdict(verification_contract=contract, executed_checks=[])
    assert v["value"] == "blocked"
    assert any(r["code"] == "required_check_not_run" for r in v["reasons"])


def test_verdict_blocked_when_required_failed():
    contract = {
        "required": [{"command": "pytest", "kind": "test", "reason": "auth_file_changed"}],
        "skipped": [],
    }
    v = compute_verdict(
        verification_contract=contract,
        executed_checks=[{"command": "pytest", "status": "fail", "evidence": "3 tests failed"}],
    )
    assert v["value"] == "blocked"
    assert any(r["code"] == "required_check_failed" for r in v["reasons"])


def test_verdict_needs_review_for_high_risk():
    contract = {"required": [], "skipped": []}
    v = compute_verdict(
        verification_contract=contract,
        risk={"level": "high", "paths": ["src/billing/charge.py"], "reasons": ["billing path"]},
    )
    assert v["value"] == "needs_review"
    assert any(r["code"] == "high_risk_path" for r in v["reasons"])


def test_verdict_pass_with_warnings_for_optimizer_findings():
    contract = {"required": [], "skipped": []}
    v = compute_verdict(
        verification_contract=contract,
        optimizer_findings=[
            {"task": "duplicated-helper", "subject": "fmt_date", "severity": "low"},
        ],
    )
    assert v["value"] == "pass_with_warnings"
    assert any(r["code"] == "optimizer_warning" for r in v["reasons"])


def test_verdict_precedence_blocked_beats_needs_review():
    contract = {"required": [{"command": "pytest", "kind": "test", "reason": "auth_file_changed"}], "skipped": []}
    v = compute_verdict(
        verification_contract=contract,
        executed_checks=[],  # not run → blocked
        risk={"level": "high", "paths": ["src/auth/session.py"], "reasons": ["auth"]},
    )
    assert v["value"] == "blocked"  # most-severe wins


def test_verdict_exit_codes():
    assert verdict_exit_code("pass") == 0
    assert verdict_exit_code("pass_with_warnings") == 0
    assert verdict_exit_code("needs_review") == 4
    assert verdict_exit_code("blocked") == 5


def test_verdict_closed_enum():
    """No surprise verdict values leak out."""
    contract = {"required": [], "skipped": []}
    for inputs in [
        {"verification_contract": contract},
        {"verification_contract": contract, "optimizer_findings": [{"task": "x", "severity": "low"}]},
        {"verification_contract": contract, "risk": {"level": "high", "paths": ["x"]}},
    ]:
        v = compute_verdict(**inputs)
        assert v["value"] in VERDICTS


# ---- reason aggregation tests ----


def test_reasons_collapse_when_same_cause():
    """Multiple required_check_not_run records with the same cause group into one."""
    from roam.verdict import aggregate_reasons

    raw = [
        {"code": "required_check_not_run", "check": "test1", "because": "auth"},
        {"code": "required_check_not_run", "check": "test2", "because": "auth"},
        {"code": "required_check_not_run", "check": "test3", "because": "auth"},
    ]
    out = aggregate_reasons(raw)
    assert len(out) == 1
    assert out[0]["code"] == "required_checks_not_run"
    assert out[0]["count"] == 3
    assert out[0]["because"] == "auth"
    assert len(out[0]["checks"]) == 3


def test_reasons_dont_collapse_different_causes():
    """Different `because` values stay as separate aggregated groups."""
    from roam.verdict import aggregate_reasons

    raw = [
        {"code": "required_check_not_run", "check": "test1", "because": "auth"},
        {"code": "required_check_not_run", "check": "test2", "because": "migrations"},
    ]
    out = aggregate_reasons(raw)
    assert len(out) == 2  # different causes → not collapsed


def test_reasons_pass_through_codes_without_grouping():
    """Codes not in GROUP_KEYS pass through unmodified."""
    from roam.verdict import aggregate_reasons

    raw = [
        {"code": "high_risk_path", "paths": ["src/auth/x.py"]},
        {"code": "all_required_passed"},
    ]
    out = aggregate_reasons(raw)
    assert len(out) == 2
    assert out[0]["code"] == "high_risk_path"
    assert out[1]["code"] == "all_required_passed"


def test_compute_verdict_aggregates_redundant_reasons():
    """End-to-end: compute_verdict returns aggregated reasons."""
    contract = {
        "required": [
            {"command": "pytest", "kind": "test", "reason": "auth_file_changed"},
            {"command": "lint", "kind": "test", "reason": "auth_file_changed"},
            {"command": "smoke", "kind": "test", "reason": "auth_file_changed"},
        ],
        "skipped": [],
    }
    v = compute_verdict(verification_contract=contract, executed_checks=[])
    # 3 missing required, all same cause → single aggregated reason
    aggregated = [r for r in v["reasons"] if r.get("code") == "required_checks_not_run"]
    assert len(aggregated) == 1
    assert aggregated[0]["count"] == 3


# ---- W1441: status-less tests_run records are claims, not evidence ----


def test_verdict_blocked_when_required_matched_only_by_unverified():
    """A tests_run record with no status used to default to "pass" and
    silently satisfy a required check by name membership (fail-open)."""
    contract = {
        "required": [{"command": "pytest", "kind": "test", "reason": "auth_file_changed"}],
        "skipped": [],
    }
    v = compute_verdict(
        verification_contract=contract,
        executed_checks=[{"command": "pytest", "status": "unverified"}],
    )
    assert v["value"] == "blocked"
    assert any(r["code"] == "required_check_unverified" for r in v["reasons"])
    # And crucially: it does NOT read as satisfied.
    assert not any(r["code"] == "all_required_passed" for r in v["reasons"])


def test_verdict_unverified_nonrequired_does_not_block():
    """An unverified record naming a non-required command is informational."""
    contract = {
        "required": [{"command": "pytest", "kind": "test", "reason": "auth_file_changed"}],
        "skipped": [],
    }
    v = compute_verdict(
        verification_contract=contract,
        executed_checks=[
            {"command": "pytest", "status": "pass"},
            {"command": "ruff check", "status": "unverified"},
        ],
    )
    assert v["value"] == "pass"


def test_proof_bundle_statusless_tests_run_maps_to_unverified():
    """The extraction seam itself: no status/result field -> unverified, never pass."""
    from roam.proof_bundle import _extract_executed_checks

    checks = _extract_executed_checks({"tests_run": [{"command": "pytest -k auth", "output": "claimed done"}]})
    assert checks == [
        {
            "command": "pytest -k auth",
            "status": "unverified",
            "evidence": "claimed done",
        }
    ]


# ---- W1443: cross-family review obligations gate the verdict ----


def _contract():
    return {"required": [{"command": "pytest", "kind": "test", "reason": "auth_file_changed"}], "skipped": []}


def _ran():
    return [{"command": "pytest", "status": "pass"}]


def test_review_gate_is_inactive_for_callers_that_do_not_opt_in():
    """REGRESSION GUARD: every proof-bundle path that predates this gate
    passes review_evidence=None and must NOT become blocked."""
    v = compute_verdict(verification_contract=_contract(), executed_checks=_ran())
    assert v["value"] == "pass"


def test_review_gate_blocks_when_opted_in_and_evidence_absent():
    v = compute_verdict(verification_contract=_contract(), executed_checks=_ran(), review_evidence={})
    assert v["value"] == "blocked"
    codes = {r["code"] for r in v["reasons"]}
    assert "plan_critique_not_run" in codes and "done_verdict_not_run" in codes


def test_review_gate_passes_on_two_declared_accepts():
    v = compute_verdict(
        verification_contract=_contract(),
        executed_checks=_ran(),
        review_evidence={
            "1b_plan_critique": {"status": "declared_accepted"},
            "4b_done_verdict": {"status": "declared_accepted"},
        },
    )
    assert v["value"] == "pass"


@pytest.mark.parametrize(
    "status,code",
    [
        ("receipt_missing", "review_receipt_missing"),
        ("receipt_malformed", "review_receipt_malformed"),
        ("wrong_phase", "review_wrong_phase"),
        ("artifact_stale", "review_artifact_stale"),
        ("same_family", "cross_family_violation"),
        ("family_unresolved", "review_family_unresolved"),
        ("rejected", "review_rejected"),
        ("review_error", "review_errored"),
    ],
)
def test_every_verifier_status_maps_to_its_own_blocker(status, code):
    v = compute_verdict(
        verification_contract=_contract(),
        executed_checks=_ran(),
        review_evidence={
            "1b_plan_critique": {"status": status, "reason": "x"},
            "4b_done_verdict": {"status": "declared_accepted"},
        },
    )
    assert v["value"] == "blocked"
    assert code in {r["code"] for r in v["reasons"]}


def test_status_blocker_mapping_is_total_over_the_verifier_vocabulary():
    """A new verifier status with no blocker must fail loudly, not pass."""
    from roam.review_receipt import REVIEW_STATUSES
    from roam.verdict import _REVIEW_STATUS_BLOCKERS

    non_accepted = set(REVIEW_STATUSES) - {"declared_accepted"}
    assert non_accepted == set(_REVIEW_STATUS_BLOCKERS)
    # one-to-one: no two statuses collapse into the same blocker
    assert len(set(_REVIEW_STATUS_BLOCKERS.values())) == len(_REVIEW_STATUS_BLOCKERS)
    with pytest.raises(ValueError):
        compute_verdict(
            verification_contract=_contract(),
            executed_checks=_ran(),
            review_evidence={"1b_plan_critique": {"status": "brand_new_status"}},
        )


def test_all_review_blockers_are_registered_reason_codes():
    from roam.guard_enums import REASON_CODES
    from roam.verdict import _PHASE_ABSENT_BLOCKER, _REVIEW_STATUS_BLOCKERS

    codes = set(_REVIEW_STATUS_BLOCKERS.values()) | set(_PHASE_ABSENT_BLOCKER.values())
    assert codes <= REASON_CODES


@pytest.mark.parametrize(
    "risk,required",
    [
        (None, True),
        ({}, True),
        ({"level": "low"}, True),  # no assessment_status => not complete
        ({"assessment_status": "complete", "level": "low", "tags": []}, False),
        ({"assessment_status": "complete", "level": "high", "tags": []}, True),
        ({"assessment_status": "partial", "level": "low", "tags": []}, True),
        ({"assessment_status": "complete", "level": "low", "tags": ["security"]}, True),
        # shape errors fail CLOSED, never skip: a bare string would iterate
        # into characters and silently miss every required tag
        ({"assessment_status": "complete", "level": "low", "tags": "security"}, True),
        ({"assessment_status": "complete", "level": "spicy", "tags": []}, True),
        ("not-a-dict", True),
    ],
)
def test_review_required_predicate_fails_closed(risk, required):
    from roam.verdict import review_required

    assert review_required(risk) is required


# ---- W1443b: the gate keys on the declared contract, not the caller ----


def _declared():
    return {"schema_version": "1.0", "review_policy": "risk_gated", "obligations": ["1b...", "4b..."]}


def test_declared_obligations_block_even_when_caller_passes_nothing():
    """The hole this closes: an agent skipped the gate by omitting evidence."""
    v = compute_verdict(
        verification_contract=_contract(),
        executed_checks=_ran(),
        orchestration_contract=_declared(),
    )
    assert v["value"] == "blocked"
    codes = {r["code"] for r in v["reasons"]}
    assert "plan_critique_not_run" in codes and "done_verdict_not_run" in codes


def test_no_declared_obligations_and_no_evidence_keeps_legacy_verdict():
    """REGRESSION GUARD: work that declares nothing is not newly blocked."""
    v = compute_verdict(verification_contract=_contract(), executed_checks=_ran())
    assert v["value"] == "pass"
    v2 = compute_verdict(
        verification_contract=_contract(),
        executed_checks=_ran(),
        orchestration_contract={"schema_version": "1.0", "obligations": []},
    )
    assert v2["value"] == "pass"


def test_declared_obligations_pass_with_two_declared_accepts():
    v = compute_verdict(
        verification_contract=_contract(),
        executed_checks=_ran(),
        orchestration_contract=_declared(),
        review_evidence={
            "1b_plan_critique": {"status": "declared_accepted"},
            "4b_done_verdict": {"status": "declared_accepted"},
        },
    )
    assert v["value"] == "pass"


def test_obligations_declared_predicate():
    from roam.verdict import obligations_declared

    assert obligations_declared(_declared()) is True
    assert obligations_declared({"obligations": []}) is False
    assert obligations_declared(None) is False
    assert obligations_declared("not-a-dict") is False
