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
