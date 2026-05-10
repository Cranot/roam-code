"""Tests for JSON envelope schema versioning.

Validates that the envelope includes schema identification and version,
that the schema registry validates envelopes correctly, and that the
`roam schema` command works as expected.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli

from roam.output.formatter import ENVELOPE_SCHEMA_NAME, ENVELOPE_SCHEMA_VERSION, json_envelope
from roam.output.schema_registry import validate_envelope

# ============================================================================
# 1. test_envelope_has_schema
# ============================================================================


def test_envelope_has_schema():
    """json_envelope output includes 'schema' field."""
    env = json_envelope("test-cmd", summary={"verdict": "ok"})
    assert "schema" in env, f"Envelope missing 'schema' key. Keys: {list(env.keys())}"
    assert env["schema"] == ENVELOPE_SCHEMA_NAME


def test_envelope_has_agent_contract_block():
    """Every envelope ships a derived ``agent_contract`` block — bounded
    summary for tight-context agents that don't want to parse the full
    payload. Opt-out via ``ROAM_AGENT_CONTRACT_BLOCK=0``.
    """
    env = json_envelope(
        "test-cmd",
        summary={"verdict": "Healthy 90/100", "health_score": 90, "confidence": 0.85},
        errors=["one violation", {"message": "oops", "rule_id": "R1"}],
        next_steps=["roam debt", {"command": "roam health --baseline last"}],
    )
    ac = env.get("agent_contract")
    assert isinstance(ac, dict), f"agent_contract block missing or wrong type: {ac!r}"

    # Verdict surfaces as the first fact.
    assert ac["facts"][0].startswith("Healthy 90/100")
    # Numeric summary fields become facts.
    assert any("health_score: 90" in f for f in ac["facts"])
    # Confidence pulled directly.
    assert ac["confidence"] == 0.85
    # Risks pulled from `errors` list, capped at 3, stringified for dicts.
    assert "one violation" in ac["risks"]
    assert any("oops" in r for r in ac["risks"])
    # next_steps both string and dict forms surface in next_commands.
    assert "roam debt" in ac["next_commands"]
    assert any("roam health" in c for c in ac["next_commands"])


def test_envelope_agent_contract_can_be_disabled():
    """``ROAM_AGENT_CONTRACT_BLOCK=0`` opts out — envelope stays clean."""
    import os

    saved = os.environ.get("ROAM_AGENT_CONTRACT_BLOCK")
    try:
        os.environ["ROAM_AGENT_CONTRACT_BLOCK"] = "0"
        env = json_envelope("test-cmd", summary={"verdict": "ok"})
        assert "agent_contract" not in env, f"Disabling via env var should suppress block, got: {list(env.keys())}"
    finally:
        if saved is None:
            os.environ.pop("ROAM_AGENT_CONTRACT_BLOCK", None)
        else:
            os.environ["ROAM_AGENT_CONTRACT_BLOCK"] = saved


def test_envelope_agent_contract_bounded():
    """The block is bounded ~200 tokens — facts ≤ 5, risks ≤ 3,
    next_commands ≤ 5, individual strings truncated to 120 chars.
    """
    summary = {"verdict": "x" * 500, "confidence": 0.5}
    # Add many numeric fields to test the facts cap.
    summary.update({f"metric_{i}": i for i in range(20)})
    errors = [f"error number {i}" for i in range(10)]
    next_steps = [f"step {i}" for i in range(10)]
    env = json_envelope("test-cmd", summary=summary, errors=errors, next_steps=next_steps)
    ac = env["agent_contract"]
    assert len(ac["facts"]) <= 5
    assert len(ac["risks"]) <= 3
    assert len(ac["next_commands"]) <= 5
    # Long verdict gets truncated.
    assert all(len(f) <= 120 for f in ac["facts"])


# ============================================================================
# 2. test_envelope_has_schema_version
# ============================================================================


def test_envelope_has_schema_version():
    """json_envelope output includes 'schema_version' field."""
    env = json_envelope("test-cmd", summary={"verdict": "ok"})
    assert "schema_version" in env, f"Envelope missing 'schema_version' key. Keys: {list(env.keys())}"
    assert env["schema_version"] == ENVELOPE_SCHEMA_VERSION


# ============================================================================
# 3. test_schema_version_is_semver
# ============================================================================


def test_schema_version_is_semver():
    """schema_version matches X.Y.Z semver pattern."""
    env = json_envelope("test-cmd", summary={"verdict": "ok"})
    version = env["schema_version"]
    assert re.match(r"^\d+\.\d+\.\d+$", version), f"schema_version '{version}' does not match semver X.Y.Z"


# ============================================================================
# 4. test_schema_name
# ============================================================================


def test_schema_name():
    """schema field equals 'roam-envelope-v1'."""
    env = json_envelope("test-cmd", summary={"verdict": "ok"})
    assert env["schema"] == "roam-envelope-v1"


# ============================================================================
# 5. test_validate_valid_envelope
# ============================================================================


def test_validate_valid_envelope():
    """Validation passes for a correct envelope."""
    env = json_envelope("health", summary={"verdict": "healthy"})
    is_valid, errors = validate_envelope(env)
    assert is_valid, f"Expected valid envelope, got errors: {errors}"
    assert errors == []


# ============================================================================
# 6. test_validate_missing_field
# ============================================================================


def test_validate_missing_field():
    """Validation catches missing required fields."""
    incomplete = {
        "command": "health",
        "version": "1.0.0",
        # missing schema, schema_version, summary
    }
    is_valid, errors = validate_envelope(incomplete)
    assert not is_valid, "Expected invalid for missing fields"
    assert any("schema" in e for e in errors), f"Should report missing 'schema': {errors}"
    assert any("schema_version" in e for e in errors), f"Should report missing 'schema_version': {errors}"
    assert any("summary" in e for e in errors), f"Should report missing 'summary': {errors}"


# ============================================================================
# 7. test_validate_bad_version
# ============================================================================


def test_validate_bad_version():
    """Validation catches non-semver version string."""
    data = json_envelope("health", summary={"verdict": "ok"})
    data["schema_version"] = "not-a-version"
    is_valid, errors = validate_envelope(data)
    assert not is_valid, "Expected invalid for bad schema_version"
    assert any("semantic version" in e for e in errors), f"Should flag bad version: {errors}"


# ============================================================================
# 8. test_validate_bad_summary
# ============================================================================


def test_validate_bad_summary():
    """Validation catches non-dict summary."""
    data = json_envelope("health", summary={"verdict": "ok"})
    data["summary"] = "not-a-dict"
    is_valid, errors = validate_envelope(data)
    assert not is_valid, "Expected invalid for non-dict summary"
    assert any("summary" in e and "dict" in e for e in errors), f"Should flag bad summary: {errors}"


# ============================================================================
# 9. test_cli_schema_runs
# ============================================================================


def test_cli_schema_runs(cli_runner):
    """roam schema exits with code 0."""
    result = invoke_cli(cli_runner, ["schema"])
    assert result.exit_code == 0, f"'schema' exited with code {result.exit_code}:\n{result.output[:500]}"


# ============================================================================
# 10. test_cli_schema_json
# ============================================================================


def test_cli_schema_json(cli_runner):
    """roam --json schema produces valid JSON envelope."""
    result = invoke_cli(cli_runner, ["schema"], json_mode=True)
    assert result.exit_code == 0, f"'schema --json' exited with code {result.exit_code}:\n{result.output[:500]}"
    data = json.loads(result.output)
    assert data["command"] == "schema"
    assert "schema" in data
    assert "schema_version" in data
    assert "summary" in data
    summary = data["summary"]
    assert "verdict" in summary
    assert "schema_name" in summary
    assert "schema_version" in summary


# ============================================================================
# 11. test_cli_schema_changelog
# ============================================================================


def test_cli_schema_changelog(cli_runner):
    """roam schema --changelog shows changelog."""
    result = invoke_cli(cli_runner, ["schema", "--changelog"])
    assert result.exit_code == 0, f"'schema --changelog' exited with code {result.exit_code}:\n{result.output[:500]}"
    assert "CHANGELOG" in result.output
    assert "1.0.0" in result.output


# ============================================================================
# 12. test_cli_schema_validate
# ============================================================================


def test_cli_schema_validate(cli_runner, tmp_path):
    """roam schema --validate works with a valid file."""
    # Create a valid envelope JSON file
    env = json_envelope("health", summary={"verdict": "healthy"})
    filepath = tmp_path / "valid_envelope.json"
    filepath.write_text(json.dumps(env, indent=2, default=str))

    result = invoke_cli(cli_runner, ["schema", "--validate", str(filepath)])
    assert result.exit_code == 0, f"'schema --validate' exited with code {result.exit_code}:\n{result.output[:500]}"
    assert "valid" in result.output.lower()
