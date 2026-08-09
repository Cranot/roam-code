"""W1525 -- reachability-triage certified a run in which three of six scanners had died.

The defect. `missing_primitives` was computed as `if not env.get(key)`, a
truthiness test for "is there an envelope". But a component that hard-fails is
represented by `cmd_service_report._component_failure`, which returns a TRUTHY
dict carrying `status: "hard_failure"`, `isError: True`,
`error_code: "COMMAND_FAILED"`. So a scanner that TIMED OUT counted as PRESENT.

Measured on a scratch repo with `vulns`, `vuln-reach` and `taint` made to
return that exact object -- the literal value `_run_roam_json` returns on all
five of its production failure paths::

    EXIT_CODE                 0
    missing_primitives        []
    summary.partial_success   False
    metrics.vulnerabilities   {'critical_reachable': 0, 'reachable': 0, 'total': 0}
    metrics.taint             {'flows': 0}
    agent_contract.facts      ['0 reachable vulnerability paths']

and with a baseline written and `--gate-on-new-reachable` passed::

    GATE EXIT_CODE            0
    gate.evaluated            true
    gate.new_reachable_finding_ids  []

A security gate certified "no new reachable findings" from a run in which the
vulnerability scanner, the reachability computation and the taint scanner had
all timed out. `gate.evaluated: true` was the strongest false claim in the
envelope: it asserts a comparison this run had nothing to make.

The floored zeros came from `_summary_value(..., default=0)`. The sibling
renderer in the SAME compose had already made this call and written down why
(cmd_service_report.py: "No `0` default: on a component failure the key is
absent, and a floored zero here printed 'Taint flows: 0' in the executive
summary while sections 4 and 5 printed the em-dash for the SAME missing
field"). This module had re-introduced the floor.

THE OVER-REFUSAL THIS FIX HAD TO AVOID. `roam vulns` with no ingested scanner
report is a SUCCESS with zero findings, and that is the normal state for most
repos. Keying the new predicate on "the metric is 0" or "the payload is empty"
would newly fail every such repo's security gate on first adoption. The
detection keys STRICTLY on `isError is True` / `status == "hard_failure"`, and
the clean-baseline tests below pin that a legitimately empty scanner is
untouched: `partial_success` stays False, metrics stay 0, the gate stays 0.
"""

from __future__ import annotations

import pytest

from roam.commands.cmd_reachability_triage import (
    _component_failed,
    _component_state,
    _compose_metrics,
    _summary_value,
)
from roam.commands.cmd_service_report import _component_failure


def _ok(**summary):
    """A component envelope for a SUCCESSFUL run (possibly with no findings)."""
    return {"command": "x", "summary": dict(summary)}


# ---------------------------------------------------------------------------
# The predicate -- the whole safety of the fix sits here
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "state",
    [
        "component_timeout",
        "component_unavailable",
        "component_output_oversized",
        "report_deadline_exhausted",
        "component_internal_failure",
    ],
)
def test_every_constructed_failure_state_is_detected(state):
    """All five states `_run_roam_json` can construct must read as failure."""
    env = _component_failure("vulns", state, "detail")
    assert _component_failed(env) is True
    assert _component_state(env) == state


def test_a_failure_envelope_is_truthy_which_is_why_the_old_test_missed_it():
    """Pins the mechanism, so a future `if not env` regression is caught."""
    env = _component_failure("taint", "component_timeout", "timed out")
    assert bool(env) is True  # the old `if not env.get(key)` saw this as PRESENT
    assert env["isError"] is True
    assert env["status"] == "hard_failure"


@pytest.mark.parametrize(
    "env",
    [
        _ok(total=0),
        _ok(total_findings=0),
        _ok(),
        {},
        {"summary": {}, "vulnerabilities": []},
        None,
        "not-a-dict",
    ],
)
def test_empty_but_successful_components_are_not_failures(env):
    """THE over-refusal boundary: emptiness is a RESULT, never a failure.

    `roam vulns` with no ingested scanner report reports zero findings and
    succeeds. If this returned True, every repo without an ingested vuln
    report would start failing its security gate.
    """
    assert _component_failed(env) is False


# ---------------------------------------------------------------------------
# Floored metrics -- an absent measurement is not a zero
# ---------------------------------------------------------------------------


def test_failed_component_metric_is_none_not_zero():
    failed = _component_failure("taint", "component_timeout", "timed out")
    assert _summary_value(failed, "findings") is None
    assert _summary_value(failed, "findings", default=0) is None


def test_successful_empty_component_metric_is_still_zero():
    """A measured zero must stay a zero. Only failures become None."""
    assert _summary_value(_ok(findings=0), "findings") == 0
    assert _summary_value(_ok(), "findings") == 0


def test_compose_metrics_reports_unknown_for_failed_scanners():
    env = {
        "sbom": _ok(total_dependencies=5, reachable_count=2),
        "supply_chain": _ok(total_dependencies=5, risk_score=1, unpinned_count=0),
        "vulns": _component_failure("vulns", "component_timeout", "timed out"),
        "vuln_reach": _component_failure("vuln-reach", "component_timeout", "timed out"),
        "taint": _component_failure("taint", "component_timeout", "timed out"),
        "secrets": _ok(total_findings=0),
    }
    metrics = _compose_metrics(env)
    assert metrics["vulnerabilities"]["total"] is None
    assert metrics["vulnerabilities"]["reachable"] is None
    assert metrics["vulnerabilities"]["critical_reachable"] is None
    assert metrics["taint"]["flows"] is None
    # Components that SUCCEEDED keep their real figures, including real zeros.
    assert metrics["dependencies"]["total"] == 5
    assert metrics["secrets"]["findings"] == 0


def test_compose_metrics_unchanged_when_every_component_succeeds():
    """The clean run must be byte-identical to its pre-fix shape."""
    env = {
        "sbom": _ok(total_dependencies=0, reachable_count=0),
        "supply_chain": _ok(total_dependencies=0, risk_score=0, unpinned_count=0),
        "vulns": _ok(total=0),
        "vuln_reach": _ok(total_vulns=0, reachable_count=0, critical_count=0),
        "taint": _ok(findings=0),
        "secrets": _ok(total_findings=0),
    }
    metrics = _compose_metrics(env)
    assert metrics["vulnerabilities"] == {"total": 0, "reachable": 0, "critical_reachable": 0}
    assert metrics["taint"] == {"flows": 0}
    assert metrics["secrets"] == {"findings": 0}
    assert all(value is not None for group in metrics.values() for value in group.values())


def test_component_state_falls_back_to_error_code():
    """A failure envelope without a state still names something, not 'unknown'."""
    env = {"isError": True, "error_code": "COMMAND_FAILED", "summary": {}}
    assert _component_state(env) == "COMMAND_FAILED"


# ---------------------------------------------------------------------------
# End to end -- envelope + gate. `_GATHER` is patched so no component
# subprocess is spawned: this file must stay fast, and the behaviour under
# test is the projection, not the scanners.
# ---------------------------------------------------------------------------


def _healthy_env():
    return {
        "sbom": _ok(total_dependencies=0, reachable_count=0),
        "supply_chain": _ok(total_dependencies=0, risk_score=0, unpinned_count=0),
        "vulns": _ok(total=0),
        "vuln_reach": _ok(total_vulns=0, reachable_count=0, critical_count=0),
        "taint": _ok(findings=0),
        "secrets": _ok(total_findings=0),
    }


def _degraded_env():
    env = _healthy_env()
    for key, name in (("vulns", "vulns"), ("vuln_reach", "vuln-reach"), ("taint", "taint")):
        env[key] = _component_failure(name, "component_timeout", "timed out after 180s")
    return env


@pytest.fixture
def triage_project(tmp_path, monkeypatch):
    from tests.conftest import git_init, index_in_process

    proj = tmp_path / "w1525_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text("def main():\n    return 1\n")
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, out
    return proj


def _patch_env(monkeypatch, env):
    from roam.commands import cmd_reachability_triage as rt

    monkeypatch.setitem(rt._GATHER, "reachability-triage", lambda commit_range: env)


def _run(cli_runner, proj, args):
    from tests.conftest import invoke_cli

    return invoke_cli(cli_runner, ["reachability-triage", *args], cwd=proj)


def _json(result):
    import json as _json_mod

    return _json_mod.loads(result.output[result.output.index("{") :])


# --- MUST FIRE -------------------------------------------------------------


def test_failed_components_are_named_in_the_envelope(cli_runner, triage_project, monkeypatch):
    _patch_env(monkeypatch, _degraded_env())
    data = _json(_run(cli_runner, triage_project, ["--json"]))
    assert [item["primitive"] for item in data["failed_primitives"]] == ["vulns", "vuln-reach", "taint"]
    assert {item["state"] for item in data["failed_primitives"]} == {"component_timeout"}
    assert data["summary"]["partial_success"] is True
    assert data["summary"]["scan_incomplete"] is True
    assert "INCOMPLETE" in data["summary"]["verdict"]


def test_absent_measurements_are_not_published_as_zero(cli_runner, triage_project, monkeypatch):
    _patch_env(monkeypatch, _degraded_env())
    data = _json(_run(cli_runner, triage_project, ["--json"]))
    assert data["metrics"]["vulnerabilities"]["reachable"] is None
    assert data["metrics"]["taint"]["flows"] is None
    assert data["agent_contract"]["facts"] != ["0 reachable vulnerability paths"]
    assert "not computed" in data["agent_contract"]["facts"][0]


def test_gate_refuses_when_the_reachability_scan_did_not_run(cli_runner, triage_project, monkeypatch):
    _patch_env(monkeypatch, _healthy_env())
    assert _run(cli_runner, triage_project, ["--json", "--write-baseline"]).exit_code == 0
    _patch_env(monkeypatch, _degraded_env())
    result = _run(cli_runner, triage_project, ["--json", "--gate-on-new-reachable"])
    assert result.exit_code == 5, result.output
    gate = _json(result)["gate"]
    assert gate["evaluated"] is False
    assert "vuln-reach did not run" in gate["unevaluated_reason"]


def test_text_channel_says_the_gate_was_not_evaluated(cli_runner, triage_project, monkeypatch):
    _patch_env(monkeypatch, _healthy_env())
    assert _run(cli_runner, triage_project, ["--json", "--write-baseline"]).exit_code == 0
    _patch_env(monkeypatch, _degraded_env())
    result = _run(cli_runner, triage_project, ["--gate-on-new-reachable"])
    assert result.exit_code == 5, result.output
    assert "NOT EVALUATED" in result.output
    assert "--" in result.output  # em-dash figures, not floored zeros


# --- MUST NOT FIRE ---------------------------------------------------------


def test_healthy_run_with_empty_scanners_is_untouched(cli_runner, triage_project, monkeypatch):
    """The normal repo: every component succeeded, all findings zero."""
    _patch_env(monkeypatch, _healthy_env())
    result = _run(cli_runner, triage_project, ["--json"])
    assert result.exit_code == 0, result.output
    data = _json(result)
    assert data["failed_primitives"] == []
    assert data["missing_primitives"] == []
    assert data["summary"]["partial_success"] is False
    assert data["metrics"]["vulnerabilities"] == {"total": 0, "reachable": 0, "critical_reachable": 0}
    assert data["agent_contract"]["facts"] == ["0 reachable vulnerability paths"]


def test_healthy_gate_still_authorizes(cli_runner, triage_project, monkeypatch):
    """A gate over a run that DID complete must still pass. The key case."""
    _patch_env(monkeypatch, _healthy_env())
    assert _run(cli_runner, triage_project, ["--json", "--write-baseline"]).exit_code == 0
    result = _run(cli_runner, triage_project, ["--json", "--gate-on-new-reachable"])
    assert result.exit_code == 0, result.output
    data = _json(result)
    assert data["gate"]["evaluated"] is True
    assert "unevaluated_reason" not in data["gate"]
    assert data["summary"]["partial_success"] is False


def test_degraded_run_without_the_gate_flag_still_exits_zero(cli_runner, triage_project, monkeypatch):
    """No gate was requested, so there is nothing to refuse -- only to disclose."""
    _patch_env(monkeypatch, _degraded_env())
    result = _run(cli_runner, triage_project, ["--json"])
    assert result.exit_code == 0, result.output
    assert _json(result)["summary"]["partial_success"] is True
