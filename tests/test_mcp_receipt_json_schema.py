"""MCP-P2.2: drift + conformance guards for the McpDecisionReceipt JSON Schema.

The schema lives at :mod:`roam.evidence.mcp_receipt_schema`; the
exporter script at :mod:`scripts.export_mcp_receipt_schema`.

Drift surface this test pins:

* Every closed-enum vocabulary the receipt references must appear in
  ``$defs`` with a membership identical to the canonical frozenset in
  :mod:`roam.evidence._vocabulary` (and the ``_POLICY_DECISIONS``
  subset in :mod:`roam.evidence.mcp_receipt`).
* Every receipt dataclass field must appear as a schema property. New
  dataclass fields without a matching schema entry are a hard fail.
* The schema validates a real :class:`McpDecisionReceipt` instance
  (serialised via ``dataclasses.asdict``).
* The export script prints valid JSON and the bytes parse back into
  the same dict the in-process schema returns.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
from pathlib import Path

import pytest

from roam.evidence._vocabulary import REDACTION_REASONS
from roam.evidence.mcp_receipt import (
    _POLICY_DECISIONS,
    McpDecisionReceipt,
    hash_input_args,
)
from roam.evidence.mcp_receipt_schema import (
    SCHEMA_ID,
    SCHEMA_VERSION,
    mcp_receipt_json_schema,
)

# ---------------------------------------------------------------------------
# Schema shape invariants
# ---------------------------------------------------------------------------


def test_schema_envelope_metadata() -> None:
    schema = mcp_receipt_json_schema()
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == SCHEMA_ID
    assert schema["version"] == SCHEMA_VERSION
    assert schema["type"] == "object"
    # Gateways depend on closed shapes.
    assert schema["additionalProperties"] is False


def test_schema_required_fields() -> None:
    schema = mcp_receipt_json_schema()
    assert set(schema["required"]) == {"tool_call", "client_id", "tool_name"}


def test_schema_id_is_versioned() -> None:
    # ``v1`` suffix lets gateways pin and detect breaking-change bumps.
    assert SCHEMA_ID.endswith("/v1.json")


# ---------------------------------------------------------------------------
# Closed-enum drift guards
# ---------------------------------------------------------------------------


def test_policy_decision_enum_matches_canonical() -> None:
    schema = mcp_receipt_json_schema()
    schema_enum = set(schema["$defs"]["PolicyDecision"]["enum"])
    assert schema_enum == set(_POLICY_DECISIONS), (
        "PolicyDecision enum drift: schema diverged from _POLICY_DECISIONS subset"
    )


def test_redaction_reason_enum_matches_canonical() -> None:
    schema = mcp_receipt_json_schema()
    schema_enum = set(schema["$defs"]["RedactionReason"]["enum"])
    assert schema_enum == set(REDACTION_REASONS), "RedactionReason enum drift: schema diverged from REDACTION_REASONS"


# ---------------------------------------------------------------------------
# Dataclass <-> schema field parity
# ---------------------------------------------------------------------------


def test_schema_covers_every_dataclass_field() -> None:
    """Adding a field to ``McpDecisionReceipt`` MUST force a schema update."""
    schema = mcp_receipt_json_schema()
    schema_fields = set(schema["properties"].keys())
    dataclass_fields = {f.name for f in dataclasses.fields(McpDecisionReceipt)}
    missing = dataclass_fields - schema_fields
    assert not missing, f"Schema missing dataclass fields: {sorted(missing)}. Update mcp_receipt_schema.py."
    extra = schema_fields - dataclass_fields
    assert not extra, (
        f"Schema declares fields not on dataclass: {sorted(extra)}. Either drop the property or add the field."
    )


# ---------------------------------------------------------------------------
# Validation against a live receipt
# ---------------------------------------------------------------------------


def _sample_receipt() -> McpDecisionReceipt:
    return McpDecisionReceipt(
        tool_call="call-001",
        client_id="claude-code-pid-1234",
        tool_name="roam_preflight",
        actor_ref_id="agent:claude-code",
        declared_side_effects=("read_only",),
        required_mode="read_only",
        input_hash=hash_input_args({"symbol": "handleSave"}),
        policy_decision="allow",
        output_hash="a" * 64,
        run_event_id="evt-42",
        redactions=("size_limit",),
        extra={"latency_ms": 12.3},
    )


def test_schema_validates_live_receipt() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema = mcp_receipt_json_schema()
    payload = json.loads(_sample_receipt().to_canonical_json())
    jsonschema.validate(instance=payload, schema=schema)


def test_schema_rejects_unknown_field() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema = mcp_receipt_json_schema()
    payload = json.loads(_sample_receipt().to_canonical_json())
    payload["this_is_not_a_real_field"] = True
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=payload, schema=schema)


def test_schema_rejects_unknown_policy_decision() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema = mcp_receipt_json_schema()
    payload = json.loads(_sample_receipt().to_canonical_json())
    payload["policy_decision"] = "approved"  # not in the canonical set
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=payload, schema=schema)


def test_schema_rejects_non_hex_input_hash() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema = mcp_receipt_json_schema()
    payload = json.loads(_sample_receipt().to_canonical_json())
    payload["input_hash"] = "not-a-hash"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=payload, schema=schema)


# ---------------------------------------------------------------------------
# Export-script smoke
# ---------------------------------------------------------------------------


def _script_path() -> Path:
    return Path(__file__).resolve().parent.parent / "scripts" / "export_mcp_receipt_schema.py"


def test_export_script_prints_valid_json() -> None:
    result = subprocess.run(
        [sys.executable, str(_script_path())],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    parsed = json.loads(result.stdout)
    in_process = mcp_receipt_json_schema()
    # Same dict either way.
    assert parsed == in_process
