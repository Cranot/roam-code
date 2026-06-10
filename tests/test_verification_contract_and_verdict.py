"""Tests for G3 verification_contract + closed-enum verdict engine.

Per project_pivot_to_roam_guard memo: these are the two new modules feeding
the AgentChangeProofBundle v1 schema emission.
"""

from __future__ import annotations

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
