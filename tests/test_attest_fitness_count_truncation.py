"""A signed attestation may not name a count smaller than what it measured.

``_collect_fitness_evidence`` slices ``violations[:50]``, and
``_compute_verdict`` then derived the reported number with
``len(fitness_data["violations"])`` -- over the TRUNCATED list, not the real
one. The evidence block carried only ``{rules, violations}``: no total, no
truncated flag, no cap disclosure anywhere in the envelope. With ``--sign``
that number is content-hashed into a tamper-evident artifact.

Measured on roam-code's own index, with every indexed file in scope and the
repo's own ``.roam/fitness.yaml``::

    files in scope: 4971
    TRUE violations from the checker:                909
    violations the attestation carried:               50
    attest evidence keys:              ['rules', 'violations']
    verdict warnings:  ['50 fitness violations in changed files']
    text header:       FITNESS VIOLATIONS (50):

``_collect_test_evidence`` in the SAME module already gets this right: it
caps ``tests[:50]`` and publishes ``selected: len(test_results)``. So the
correct shape existed one function away.

WHAT IS NOT COVERED HERE
------------------------
The cap itself is kept. An attestation that grows without bound is its own
problem, and the finding is that the cap was UNDISCLOSED, not that it
existed. Nothing here asserts a particular cap value beyond the module
constant, so raising or lowering it stays a free decision.
"""

from __future__ import annotations

import pytest

from roam.commands.cmd_attest import (
    _FITNESS_EVIDENCE_CAP,
    _append_attestation_fitness_section,
    _collect_fitness_evidence,
    _compute_verdict,
)


def _violation(i: int) -> dict:
    return {"rule": f"rule-{i}", "message": f"violation {i}", "file": f"src/f{i}.py"}


@pytest.fixture()
def over_cap(monkeypatch: pytest.MonkeyPatch) -> int:
    """Make the checker return more violations than the evidence cap holds."""
    total = _FITNESS_EVIDENCE_CAP * 3 + 7
    from roam.commands import cmd_diff

    monkeypatch.setattr(
        cmd_diff,
        "_collect_fitness_violations",
        lambda conn, file_map, root: ([], [_violation(i) for i in range(total)]),
    )
    return total


def test_evidence_publishes_the_total_it_truncated(over_cap: int) -> None:
    evidence = _collect_fitness_evidence(conn=None, file_map={}, root=None)

    assert len(evidence["violations"]) == _FITNESS_EVIDENCE_CAP, "the cap is kept -- this fix discloses it"
    assert evidence["violations_total"] == over_cap, evidence
    assert evidence["violations_truncated"] is True, evidence


def test_the_verdict_counts_the_measurement_not_the_slice(over_cap: int) -> None:
    evidence = _collect_fitness_evidence(conn=None, file_map={}, root=None)

    verdict = _compute_verdict({"total": 0}, {"failed": 0}, evidence, {})

    assert f"{over_cap} fitness violations in changed files" in verdict["warnings"], verdict["warnings"]
    assert not any(f"{_FITNESS_EVIDENCE_CAP} fitness violations" in w for w in verdict["warnings"]), (
        "the signed verdict still reports the truncated count"
    )


def test_the_rendered_section_names_the_total_and_says_it_is_partial(over_cap: int) -> None:
    evidence = _collect_fitness_evidence(conn=None, file_map={}, root=None)
    lines: list[str] = []

    _append_attestation_fitness_section(
        lines,
        {
            "fitness_violations": evidence["violations"],
            "fitness_violations_total": evidence["violations_total"],
        },
        False,
    )

    assert lines[0] == f"FITNESS VIOLATIONS ({over_cap}):", lines[0]
    assert any(f"first {_FITNESS_EVIDENCE_CAP} of {over_cap}" in line for line in lines), lines


def test_an_under_cap_run_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """The must-not-fire control.

    Below the cap the total IS the list length, so nothing about the
    attestation may move -- no truncation note, no changed header, no
    changed warning. Without this the fix could add noise to every ordinary
    attestation.
    """
    from roam.commands import cmd_diff

    monkeypatch.setattr(
        cmd_diff,
        "_collect_fitness_violations",
        lambda conn, file_map, root: ([], [_violation(i) for i in range(3)]),
    )
    evidence = _collect_fitness_evidence(conn=None, file_map={}, root=None)

    assert evidence["violations_total"] == 3
    assert evidence["violations_truncated"] is False
    verdict = _compute_verdict({"total": 0}, {"failed": 0}, evidence, {})
    assert "3 fitness violations in changed files" in verdict["warnings"]

    lines: list[str] = []
    _append_attestation_fitness_section(
        lines,
        {"fitness_violations": evidence["violations"], "fitness_violations_total": 3},
        False,
    )
    assert lines[0] == "FITNESS VIOLATIONS (3):", lines[0]
    assert not any("first" in line for line in lines), lines


def test_a_failed_collection_reports_zero_not_a_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The defensive arm must still produce the new fields.

    A ``None`` total would flow into the verdict comparison and raise; the
    error path has to be as well-formed as the success path.
    """
    from roam.commands import cmd_diff

    def _boom(conn, file_map, root):
        raise RuntimeError("fitness checker exploded")

    monkeypatch.setattr(cmd_diff, "_collect_fitness_violations", _boom)
    evidence = _collect_fitness_evidence(conn=None, file_map={}, root=None)

    assert evidence == {"rules": [], "violations": [], "violations_total": 0, "violations_truncated": False}
    assert _compute_verdict({"total": 0}, {"failed": 0}, evidence, {})["warnings"] == []


def test_legacy_evidence_without_the_field_still_computes(over_cap: int) -> None:
    """Evidence built before this field existed must not crash the verdict.

    The attestation format is consumed by other tools; a hard KeyError on an
    older payload would turn a disclosure fix into an outage.
    """
    legacy = {"rules": [], "violations": [_violation(i) for i in range(4)]}
    verdict = _compute_verdict({"total": 0}, {"failed": 0}, legacy, {})
    assert "4 fitness violations in changed files" in verdict["warnings"]
