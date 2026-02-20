"""Tests for JSON envelope schema versioning.

Validates that the envelope includes schema identification and version,
that the schema registry validates envelopes correctly, and that the
`roam schema` command works as expected.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli

from roam.output.formatter import json_envelope, ENVELOPE_SCHEMA_VERSION, ENVELOPE_SCHEMA_NAME
from roam.output.schema_registry import get_schema_info, validate_envelope


# ============================================================================
# 1. test_envelope_has_schema
# ============================================================================

def test_envelope_has_schema():
    """json_envelope output includes 'schema' field."""
    env = json_envelope("test-cmd", summary={"verdict": "ok"})
    assert "schema" in env, f"Envelope missing 'schema' key. Keys: {list(env.keys())}"
    assert env["schema"] == ENVELOPE_SCHEMA_NAME


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
    assert re.match(r"^\d+\.\d+\.\d+$", version), (
        f"schema_version '{version}' does not match semver X.Y.Z"
    )


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
        # missing schema, schema_version, timestamp, summary
    }
    is_valid, errors = validate_envelope(incomplete)
    assert not is_valid, "Expected invalid for missing fields"
    assert any("schema" in e for e in errors), f"Should report missing 'schema': {errors}"
    assert any("schema_version" in e for e in errors), f"Should report missing 'schema_version': {errors}"
    assert any("timestamp" in e for e in errors), f"Should report missing 'timestamp': {errors}"
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
    assert result.exit_code == 0, (
        f"'schema' exited with code {result.exit_code}:\n{result.output[:500]}"
    )


# ============================================================================
# 10. test_cli_schema_json
# ============================================================================

def test_cli_schema_json(cli_runner):
    """roam --json schema produces valid JSON envelope."""
    result = invoke_cli(cli_runner, ["schema"], json_mode=True)
    assert result.exit_code == 0, (
        f"'schema --json' exited with code {result.exit_code}:\n{result.output[:500]}"
    )
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
    assert result.exit_code == 0, (
        f"'schema --changelog' exited with code {result.exit_code}:\n{result.output[:500]}"
    )
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
    assert result.exit_code == 0, (
        f"'schema --validate' exited with code {result.exit_code}:\n{result.output[:500]}"
    )
    assert "valid" in result.output.lower()
