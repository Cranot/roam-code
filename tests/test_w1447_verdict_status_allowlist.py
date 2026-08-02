"""W1447 — only a check RECORDED AS PASSING can satisfy a required check.

W1441 fixed the status-less-record fail-open one layer down, in
``proof_bundle._extract_executed_checks``. It expressed satisfaction as a
DENYLIST in ``verdict._collect_blockers_that_invalidate_proof``::

    executed_names = {c["command"] for c in executed_checks if c.get("status") != "unverified"}

A denylist only refuses the values it has been taught to distrust. Measured
against the shipped 13.10.0 binary, every one of these returned exit 0 and
``all_required_passed`` on a bundle whose sole record was NOT evidence of a
pass:

    status "skipped"      -> pass    (a status nobody claims means success)
    status absent         -> pass    (the exact shape W1441 names)
    status "Unverified"   -> pass    (capitalisation defeated the denylist)
    status null           -> pass

``proof_bundle.validate_v1`` already rejected those shapes against
``CHECK_STATUSES``, but ``cmd_verdict`` never calls it — so the two validators
disagreed about the same bundle and the disagreement was invisible.

The fix inverts the direction: satisfaction is an ALLOWLIST on ``"pass"``, so a
status nobody has invented yet fails closed instead of open.

Every test below carries its negative control in the same file. A gate that
blocks everything is not fixed, it is broken in the other direction, so
``test_a_genuinely_passing_check_still_passes`` is load-bearing: without it,
deleting the whole satisfaction check would make this suite green.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from roam.guard_enums import CHECK_STATUSES
from roam.verdict import compute_verdict

CONTRACT = {"required": [{"command": "pytest -q", "reason": "tests must pass"}], "skipped": []}


def _verdict_for(status_record: dict | None) -> dict:
    """Compute a verdict for a contract requiring one check."""
    checks = [] if status_record is None else [status_record]
    return compute_verdict(verification_contract=CONTRACT, executed_checks=checks)


def _codes(v: dict) -> set[str]:
    return {r.get("code") for r in v.get("reasons", [])}


# Statuses that are NOT the string "pass". None of them may satisfy a
# requirement. Parameterised rather than looped so a failure names the value.
@pytest.mark.parametrize(
    "status",
    [
        "skipped",
        "Unverified",  # capitalisation — defeated the old denylist
        "PASS",  # wrong case: "pass" is the literal, not a case-insensitive match
        "passed",  # near-miss spelling
        None,
        "",
        "ok",
        "success",
        "unknown-status-nobody-has-invented-yet",
    ],
)
def test_non_pass_status_never_satisfies_a_required_check(status: str | None) -> None:
    """The allowlist property: anything that is not "pass" fails closed."""
    v = _verdict_for({"command": "pytest -q", "status": status})
    assert v["value"] == "blocked", f"status {status!r} satisfied a required check"
    assert _codes(v) & {
        "required_check_status_invalid",
        "required_check_unverified",
        "required_check_not_run",
    }, f"status {status!r} blocked but with no explanatory code: {_codes(v)}"


def test_missing_status_key_entirely_never_satisfies() -> None:
    """The W1441 shape, re-asserted at the layer that actually decides."""
    v = _verdict_for({"command": "pytest -q"})
    assert v["value"] == "blocked"


def test_a_genuinely_passing_check_still_passes() -> None:
    """NEGATIVE CONTROL — the gate must still let real evidence through.

    Without this, a fix that blocks unconditionally would pass every other
    test in this file.
    """
    v = _verdict_for({"command": "pytest -q", "status": "pass"})
    assert v["value"] == "pass", f"a recorded pass was refused: {_codes(v)}"
    assert _codes(v) == {"all_required_passed"}


def test_a_failing_check_is_reported_as_failed_not_as_not_run() -> None:
    """NEGATIVE CONTROL — precision, not just fail-closed.

    "fail" is not in the allowlist, so a naive inversion would ALSO emit
    ``required_check_not_run`` and report one defect as two unrelated
    reasons. The failure loop already names it precisely.
    """
    v = _verdict_for({"command": "pytest -q", "status": "fail"})
    assert v["value"] == "blocked"
    assert "required_check_failed" in _codes(v)
    assert "required_check_not_run" not in _codes(v)
    assert "required_check_status_invalid" not in _codes(v)


def test_unverified_keeps_its_own_precise_code() -> None:
    """W1441's code must survive the inversion — it is more specific."""
    v = _verdict_for({"command": "pytest -q", "status": "unverified"})
    assert v["value"] == "blocked"
    assert "required_check_unverified" in _codes(v)


def test_invalid_status_is_distinguished_from_never_run() -> None:
    """A record that EXISTS with a bad status is not the same as no record.

    Collapsing the two would tell an agent to run a check it already ran,
    instead of telling it the outcome was never recorded.
    """
    invalid = _verdict_for({"command": "pytest -q", "status": "skipped"})
    absent = _verdict_for(None)
    assert "required_check_status_invalid" in _codes(invalid)
    assert "required_check_not_run" in _codes(absent)
    assert _codes(invalid) != _codes(absent)


def test_invalid_status_reason_reports_the_offending_value() -> None:
    """The reason must carry the status, or a human cannot debug the bundle."""
    v = _verdict_for({"command": "pytest -q", "status": "skipped"})
    reason = next(r for r in v["reasons"] if r["code"] == "required_check_status_invalid")
    assert reason["status"] == "skipped"
    assert reason["check"] == "pytest -q"


def test_the_allowlist_agrees_with_the_schema_enum() -> None:
    """The two validators must not drift apart again.

    Every member of ``CHECK_STATUSES`` other than "pass" must block, and
    "pass" must be in the enum. If someone adds a status meaning success,
    this fails and forces the allowlist to be updated deliberately.
    """
    assert "pass" in CHECK_STATUSES
    for status in CHECK_STATUSES:
        v = _verdict_for({"command": "pytest -q", "status": status})
        expected = "pass" if status == "pass" else "blocked"
        assert v["value"] == expected, f"CHECK_STATUSES member {status!r} gave {v['value']}"


# ---- end-to-end through the CLI, which is the path that was actually broken ----


def _run_verdict(tmp_path: Path, bundle: dict) -> tuple[int, dict]:
    p = tmp_path / "bundle.json"
    p.write_text(json.dumps(bundle), encoding="utf-8")
    r = subprocess.run(
        [sys.executable, "-m", "roam", "--json", "verdict", "--bundle", str(p), "--strict"],
        capture_output=True,
        text=True,
    )
    return r.returncode, json.loads(r.stdout)


def test_cli_legacy_tests_run_shape_cannot_pass_without_a_status(tmp_path: Path) -> None:
    """The defect in its original form: legacy shape, read raw by the CLI.

    ``cmd_verdict._extract_contract_inputs`` read ``body["tests_run"]``
    directly instead of through the normaliser ``build_proof_bundle`` uses,
    so W1441's hardening was bypassed on exactly this path.
    """
    code, out = _run_verdict(
        tmp_path,
        {"body": {"verification_contract": CONTRACT, "tests_run": [{"command": "pytest -q"}]}},
    )
    verdict = out.get("verdict") or out.get("data", {}).get("verdict", {})
    assert verdict["value"] == "blocked"
    assert code == 5


def test_cli_still_passes_a_real_recorded_pass(tmp_path: Path) -> None:
    """NEGATIVE CONTROL at the CLI boundary."""
    code, out = _run_verdict(
        tmp_path,
        {
            "verification_contract": CONTRACT,
            "executed_checks": [{"command": "pytest -q", "status": "pass"}],
        },
    )
    verdict = out.get("verdict") or out.get("data", {}).get("verdict", {})
    assert verdict["value"] == "pass"
    assert code == 0
