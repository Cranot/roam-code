"""Tests for `roam compile` — freeform task → structured envelope.

Empirically validated 2026-05-28 (Opus 4.8 spike): facts envelope
delivers 99% of vanilla quality at 54% of cost.

"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from roam.cli import cli


@pytest.fixture
def runner():
    return CliRunner()


def test_compile_text_mode_basic(runner):
    """Text mode emits VERDICT + procedure + artifact_type."""
    result = runner.invoke(cli, ["compile", "Find files coupled to src/roam/cli.py"])
    assert result.exit_code == 0
    assert "VERDICT:" in result.output
    assert "procedure:" in result.output
    assert "artifact_type:" in result.output


def test_compile_json_mode_basic(runner):
    """JSON mode emits roam-envelope-v1 with artifact field."""
    result = runner.invoke(cli, ["--json", "compile", "Find files coupled to src/roam/cli.py"])
    assert result.exit_code == 0
    envelope = json.loads(result.output)
    assert envelope["schema"] == "roam-envelope-v1"
    assert "summary" in envelope
    assert "verdict" in envelope["summary"]
    assert "artifact" in envelope
    assert envelope["artifact"]["schema"].startswith("roam-plan-v0")


def test_compile_structural_routes_to_full(runner):
    """structural_* procedures route to `full` OR `l1_probe`.

    W33: structural tasks with named paths now prefer the
    l1_probe envelope when the probe returns procedure-specific facts
    (the agent gets the answer, not just metadata). Either is the right
    choice; the test accepts both since the index may not always have
    coverage to populate the probe.
    """
    result = runner.invoke(
        cli,
        ["--json", "compile", "Find files coupled to src/roam/cli.py"],
    )
    assert result.exit_code == 0
    envelope = json.loads(result.output)
    assert envelope["summary"]["procedure"] == "structural_coupling"
    assert envelope["summary"]["artifact_type"] in ("full", "l1_probe")


def test_compile_freeform_routes_to_facts(runner):
    """freeform_explore is policy-mapped to facts. W51 (per-procedure
    confidence thresholds) lowered the freeform threshold to 0.30 so a
    typical conf=0.35 fall-through task now lands on the specialized
    "facts" policy rather than the generic "full" fallback. The R10.1
    safety gate still applies to other procedures (trace=0.70,
    structural=0.60, stack_trace=0.85)."""
    result = runner.invoke(
        cli,
        ["--json", "compile", "investigate why login is slow"],
    )
    assert result.exit_code == 0
    envelope = json.loads(result.output)
    assert envelope["summary"]["procedure"] == "freeform_explore"
    # W51: low-conf freeform now lands on the specialized "facts" policy
    # (was "full" pre-W51 under the global 0.60 threshold).
    assert envelope["summary"]["artifact_type"] in ("facts", "l1_probe")


def test_compile_artifact_override_facts(runner):
    """--artifact facts forces facts envelope regardless of procedure."""
    result = runner.invoke(
        cli,
        ["--json", "compile", "Find files coupled to src/roam/cli.py", "--artifact", "facts"],
    )
    assert result.exit_code == 0
    envelope = json.loads(result.output)
    assert envelope["summary"]["artifact_type"] == "facts"
    assert envelope["artifact"]["schema"] == "roam-plan-v0-facts"


def test_compile_envelope_has_agent_contract(runner):
    """JSON envelope includes agent_contract.facts (LAW 4 compliance)."""
    result = runner.invoke(
        cli,
        ["--json", "compile", "Find files coupled to src/roam/cli.py"],
    )
    envelope = json.loads(result.output)
    assert "agent_contract" in envelope
    facts = envelope["agent_contract"]["facts"]
    assert len(facts) >= 3
    # Concrete-noun anchors (LAW 4)
    assert any("files" in f or "procedure" in f or "paths" in f for f in facts)


def test_compile_verify_hint_is_family_neutral_when_enabled(runner, monkeypatch):
    """Compiler Verify is an opt-in compiler phase, not an edit-family special."""
    monkeypatch.setenv("ROAM_COMPILE_VERIFY", "1")
    result = runner.invoke(
        cli,
        ["--json", "compile", "Find files coupled to src/roam/cli.py"],
    )
    assert result.exit_code == 0
    envelope = json.loads(result.output)
    assert envelope["summary"]["procedure"] == "structural_coupling"
    assert "roam verify --auto" in envelope["agent_contract"]["next_commands"]


def test_compile_verify_hint_can_be_forced_off(runner, monkeypatch):
    """The per-invocation toggle can turn Verify off even if repo config enables it."""
    monkeypatch.setenv("ROAM_COMPILE_VERIFY", "0")
    result = runner.invoke(
        cli,
        ["--json", "compile", "Find files coupled to src/roam/cli.py"],
    )
    assert result.exit_code == 0
    envelope = json.loads(result.output)
    assert "roam verify --auto" not in envelope["agent_contract"]["next_commands"]


def test_compile_agent_contract_includes_command_executability_checks(runner, monkeypatch):
    """Compiler advice carries an F3 executability sidecar without changing commands."""
    monkeypatch.setenv("ROAM_COMPILE_VERIFY", "1")
    result = runner.invoke(
        cli,
        ["--json", "compile", "Find files coupled to src/roam/cli.py"],
    )
    assert result.exit_code == 0
    envelope = json.loads(result.output)
    checks = envelope["agent_contract"]["command_checks"]
    by_source = {check["source"]: check for check in checks}

    assert by_source["artifact.plan.recommended_first_command"]["failure_class"] == "F3_executability"
    assert by_source["agent_contract.next_commands[0]"]["target_status"] == "placeholder"
    assert by_source["agent_contract.next_commands[1]"]["target_status"] == "placeholder"
    assert by_source["agent_contract.next_commands[2]"]["command_text"] == "roam verify --auto"
    assert by_source["agent_contract.next_commands[2]"]["registry_status"] == "known"
    assert by_source["agent_contract.next_commands[2]"]["parse_status"] == "parsed"
    assert by_source["agent_contract.next_commands[2]"]["executable_status"] == "checked"


def test_compile_no_model_calls(runner):
    """compile_plan is zero-model — model_calls_avoided must list reductions."""
    result = runner.invoke(
        cli,
        ["--json", "compile", "Find files coupled to src/roam/cli.py"],
    )
    envelope = json.loads(result.output)
    avoided = envelope["summary"]["model_calls_avoided"]
    assert isinstance(avoided, list)
    assert len(avoided) >= 1


# --------------------------------------------------------------------------
# Backprop: prior compile failures → next evidence packet
# --------------------------------------------------------------------------

from roam.commands.cmd_compile import (  # noqa: E402
    _compile_failure_signals,
    _next_evidence_for_compile,
    _next_evidence_hint,
)


def _write_runs(tmp_path, rows):
    """Write a synthetic .roam/compile-runs.jsonl under tmp_path."""
    import json as _json

    (tmp_path / ".roam").mkdir()
    with (tmp_path / ".roam" / "compile-runs.jsonl").open("w") as fh:
        for row in rows:
            fh.write(_json.dumps(row) + "\n")


def _task_hash(task: str) -> str:
    import hashlib

    return hashlib.sha256(task.encode("utf-8", "replace")).hexdigest()[:12]


def test_next_evidence_not_initialized_when_no_log(tmp_path):
    """Missing .roam/compile-runs.jsonl ⇒ explicit not_initialized state."""
    ev = _next_evidence_for_compile(str(tmp_path))
    assert ev["state"] == "not_initialized"
    assert ev["hint"] is None


def test_next_evidence_no_recent_failure_when_healthy(tmp_path):
    """A tail with only healthy rows ⇒ no_recent_failure, null hint."""
    _write_runs(
        tmp_path,
        [
            {
                "ts": "2026-07-01T00:00:00Z",
                "procedure": "structural_coupling",
                "classifier_conf": 0.85,
                "cache_hit": True,
                "compile_ms": 12.0,
                "task_hash": "aaa",
                "task_prefix": "healthy",
            },
        ],
    )
    ev = _next_evidence_for_compile(str(tmp_path))
    assert ev["state"] == "no_recent_failure"
    assert ev["hint"] is None


def test_next_evidence_surfaces_latest_prior_failure(tmp_path):
    """A degraded prior row backprops a recent_failure hint into the packet."""
    _write_runs(
        tmp_path,
        [
            {
                "ts": "2026-07-01T00:00:00Z",
                "procedure": "freeform_explore",
                "classifier_conf": 0.45,
                "cache_hit": False,
                "compile_ms": 1.0,
                "task_hash": "older",
                "task_prefix": "older degrade",
            },
            {
                "ts": "2026-07-02T00:00:00Z",
                "procedure": "structural_coupling",
                "classifier_conf": 0.9,
                "cache_hit": True,
                "compile_ms": 10.0,
                "task_hash": "healthy",
                "task_prefix": "healthy recent",
            },
        ],
    )
    # No current-task exclusion → the newest degraded row wins (the older one).
    ev = _next_evidence_for_compile(str(tmp_path))
    assert ev["state"] == "recent_failure"
    assert ev["from_failure_at"] == "2026-07-01T00:00:00Z"
    assert "freeform_explore" in ev["signals"]
    assert "low_confidence" in ev["signals"]
    assert ev["hint"]  # non-empty imperative hint
    assert "freeform_explore" in ev["hint"] or "target" in ev["hint"]


def test_next_evidence_excludes_current_task_row(tmp_path):
    """The current compile's own row is skipped so we backprop a PRIOR failure."""
    current = "investigate why login is slow"
    _write_runs(
        tmp_path,
        [
            {
                "ts": "2026-07-01T00:00:00Z",
                "procedure": "freeform_explore",
                "classifier_conf": 0.4,
                "cache_hit": False,
                "compile_ms": 1.0,
                "task_hash": "priorfail",
                "task_prefix": "prior degrade",
            },
            {
                "ts": "2026-07-02T00:00:00Z",
                "procedure": "freeform_explore",
                "classifier_conf": 0.4,
                "cache_hit": False,
                "compile_ms": 1.0,
                "task_hash": _task_hash(current),
                "task_prefix": "current run",
            },
        ],
    )
    ev = _next_evidence_for_compile(str(tmp_path), current_task=current)
    assert ev["state"] == "recent_failure"
    # The current run (newest) was excluded; the prior degrade surfaced.
    assert ev["from_failure_at"] == "2026-07-01T00:00:00Z"


def test_next_evidence_hint_is_empty_for_unknown_signal():
    """A failure dict with no recognized signal yields an empty hint."""
    assert _next_evidence_hint({"signals": ["bogus"]}) == ""


def test_next_evidence_signals_detect_slow_uncached():
    """cache_hit False + compile_ms >= 1500 ⇒ slow_uncached signal."""
    row = {"procedure": "structural_coupling", "classifier_conf": 0.9, "cache_hit": False, "compile_ms": 2100.0}
    assert "slow_uncached" in _compile_failure_signals(row)
    assert "slow_uncached" not in _compile_failure_signals({**row, "compile_ms": 100.0})
    assert "slow_uncached" not in _compile_failure_signals({**row, "cache_hit": True})


def test_compile_envelope_carries_next_evidence(runner):
    """The --json envelope always carries an explicit next_evidence state."""
    result = runner.invoke(cli, ["--json", "compile", "Find files coupled to src/roam/cli.py"])
    assert result.exit_code == 0
    envelope = json.loads(result.output)
    ev = envelope["summary"]["next_evidence"]
    assert isinstance(ev, dict)
    assert ev["state"] in {"recent_failure", "no_recent_failure", "not_initialized"}
    # hint is a string when there is a failure, else null — never undefined.
    assert "hint" in ev


# --------------------------------------------------------------------------
# --checklist: compose required_checks + verification_contract +
# recommended_first_command into one STATIC checklist block (#77).
# --------------------------------------------------------------------------


def test_compile_checklist_emits_composed_block(runner):
    """Synthesis task so required_checks can populate; the block composes
    recommended_first_command + required checkboxes + verification contract."""
    result = runner.invoke(
        cli,
        ["--json", "compile", "write a pytest for src/roam/plan/compiler.py::compile_plan", "--checklist"],
    )
    assert result.exit_code == 0
    block = json.loads(result.output)
    assert block["schema"] == "roam-compile-checklist-v1"
    assert block["kind"] == "static"  # honesty: static, not live
    assert "recommended_first_command" in block
    assert block["recommended_first_command"]  # non-empty routing hint
    assert "checks" in block and isinstance(block["checks"], list)
    # composed contract mirrors the proof-stub shape (cmd_compile.py:67-70)
    vc = block["verification_contract"]
    assert set(vc) >= {"required", "compiler_recommended_first"}
    # each required check surfaces as a checkbox line
    for item in block["checks"]:
        assert item["done"] is False
        assert item["check"] in vc["required"]
    # required_checks reused verbatim from the plan object (not recomputed)
    assert [i["check"] for i in block["checks"]] == vc["required"]


def test_compile_checklist_static_and_may_be_empty(runner):
    """Honesty on empty-check procedures: a structural task yields
    required_checks == [], and the block does not over-promise 'live'."""
    result = runner.invoke(
        cli,
        ["--json", "compile", "Find files coupled to src/roam/cli.py", "--checklist"],
    )
    assert result.exit_code == 0
    block = json.loads(result.output)
    assert block["kind"] == "static"
    # note states it is static and NOT live — does not over-promise
    assert "static" in block["note"].lower() and "not" in block["note"].lower()
    assert isinstance(block["checks"], list)  # empty is valid for structural_coupling
