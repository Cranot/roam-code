"""JSON Schema (Draft 2020-12) for :class:`McpDecisionReceipt`.

MCP-P2.2: export a portable, machine-readable schema describing the
receipt shape so external gateways (Interlock, Lasso, Portkey) and
procurement reviewers can validate roam-emitted receipts without
reading Python source.

The schema is constructed from the canonical vocabulary frozensets in
:mod:`roam.evidence._vocabulary` and the closed-enumeration subset
:data:`roam.evidence.mcp_receipt._POLICY_DECISIONS` at build time.
Hardcoding enum values would let vocabulary drift produce a schema
that disagrees with the live dataclass; pulling by reference keeps the
two in lock-step.

Stability contract:

* ``$id`` carries an explicit semver suffix (``v1``). Additive field
  changes stay on ``v1``; removing a field or tightening a constraint
  requires a new ``$id``.
* ``additionalProperties: false`` at the top level. Gateways depend on
  closed shapes; an unknown field is a validation error, not a soft
  warning.
* Enum vocabularies live in ``$defs`` so consumers can introspect the
  closed-enum membership directly from the schema document.

See ``dev/MCP-SECURITY-POSTURE.md`` § "Schema export" for the gateway-
facing description.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from roam.evidence._vocabulary import REDACTION_REASONS
from roam.evidence.mcp_receipt import _POLICY_DECISIONS

#: Schema identifier. Bump the ``v1`` suffix on breaking changes (field
#: removal, type tightening, enum removal). Additive changes stay on v1.
SCHEMA_ID: str = "https://roam-code.com/schema/mcp-receipt/v1.json"

#: Semver version of the schema document itself. Independent of roam's
#: package version: a roam release may ship without touching the schema.
SCHEMA_VERSION: str = "1.0.0"

#: SHA-256 hex digest pattern. Used for ``input_hash`` / ``output_hash``.
_SHA256_HEX_PATTERN: str = "^[0-9a-f]{64}$"


def mcp_receipt_json_schema() -> dict[str, Any]:
    """Return the JSON Schema document for :class:`McpDecisionReceipt`.

    Pulls closed-enum vocabulary at call time so a vocabulary edit in
    :mod:`roam.evidence._vocabulary` propagates here without a separate
    edit. The returned dict is a fresh object on every call — callers
    can mutate it (e.g. to add a ``$comment`` for downstream tooling)
    without affecting other callers.
    """
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        "title": "McpDecisionReceipt",
        "description": (
            "Local decision receipt for a sensitive MCP tool call. One "
            "receipt is produced per sensitive tool invocation so that "
            "'who invoked what tool with what args, and what did the "
            "policy layer decide?' remains locally verifiable evidence. "
            "Receipts are bundled into the broader ChangeEvidence packet "
            "downstream; this schema describes the receipt in isolation."
        ),
        "type": "object",
        "version": SCHEMA_VERSION,
        "additionalProperties": False,
        "required": ["tool_call", "client_id", "tool_name"],
        "properties": {
            "tool_call": {
                "type": "string",
                "description": "Opaque per-invocation id (caller-generated).",
                "minLength": 1,
            },
            "client_id": {
                "type": "string",
                "description": "MCP client process id.",
                "minLength": 1,
            },
            "tool_name": {
                "type": "string",
                "description": "Name of the tool invoked (e.g. 'roam_preflight').",
                "minLength": 1,
            },
            "actor_ref_id": {
                "type": ["string", "null"],
                "description": (
                    "ActorRef.actor_id when available; null until the producer has populated the identity surface."
                ),
                "default": None,
            },
            "declared_side_effects": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "What the tool declared in _TOOL_METADATA "
                    "(e.g. 'read_only', 'write_filesystem'). Free-form "
                    "string tuple; not a closed enum at the receipt "
                    "layer."
                ),
                "default": [],
            },
            "required_mode": {
                "type": ["string", "null"],
                "description": ("Mode the tool requires: read_only / safe_edit / migration / autonomous_pr."),
                "default": None,
            },
            "input_hash": {
                "type": ["string", "null"],
                "description": ("sha256 of canonical-JSON input args. Hex-encoded, 64 lowercase hex characters."),
                "pattern": _SHA256_HEX_PATTERN,
                "default": None,
            },
            "policy_decision": {
                "$ref": "#/$defs/PolicyDecision",
                "description": (
                    "Authority-gate decision the policy layer reached "
                    "for this call. Defaults to 'not_evaluated' when no "
                    "policy layer was active."
                ),
                "default": "not_evaluated",
            },
            "output_ref": {
                "type": ["string", "null"],
                "description": ("Artifact id or path when the output is large. Mutually exclusive with output_hash."),
                "default": None,
            },
            "output_hash": {
                "type": ["string", "null"],
                "description": (
                    "sha256 of inline output when small. Mutually "
                    "exclusive with output_ref. Hex-encoded, 64 "
                    "lowercase hex characters."
                ),
                "pattern": _SHA256_HEX_PATTERN,
                "default": None,
            },
            "run_event_id": {
                "type": ["string", "null"],
                "description": ("Link to .roam/runs/<id>/events.jsonl row."),
                "default": None,
            },
            "redactions": {
                "type": "array",
                "items": {"$ref": "#/$defs/RedactionReason"},
                "description": (
                    "Tuple of redaction reasons recorded against this "
                    "receipt. Each entry MUST be a member of the closed "
                    "vocabulary in $defs/RedactionReason."
                ),
                "default": [],
            },
            "extra": {
                "type": "object",
                "description": (
                    "Free-form structured detail. Forward-compatible by "
                    "construction: new keys can land without a schema "
                    "version bump. Gateways that need a structural "
                    "guarantee on a field inside extra should request "
                    "promotion to a top-level field."
                ),
                "additionalProperties": True,
                "default": {},
            },
        },
        "allOf": [
            {
                "$comment": (
                    "Mutual exclusion: output_ref carries an artifact "
                    "pointer (large output stored elsewhere); "
                    "output_hash is the digest of inline output. Having "
                    "both creates ambiguity about which is authoritative."
                ),
                "not": {
                    "type": "object",
                    "required": ["output_ref", "output_hash"],
                    "properties": {
                        "output_ref": {"type": "string"},
                        "output_hash": {"type": "string"},
                    },
                },
            },
        ],
        "$defs": {
            "PolicyDecision": {
                "type": "string",
                "description": (
                    "Authority-gate subset of the canonical "
                    "POLICY_DECISIONS vocabulary. The MCP receipt layer "
                    "produces only the authority-gate verdicts; the "
                    "rule-evaluation verdicts (pass / fail / unknown) "
                    "live at the rules-engine layer."
                ),
                "enum": sorted(_POLICY_DECISIONS),
            },
            "RedactionReason": {
                "type": "string",
                "description": (
                    "Canonical evidence-compiler redaction-reason "
                    "vocabulary. Mirrors "
                    "roam.evidence._vocabulary.REDACTION_REASONS at "
                    "schema-build time."
                ),
                "enum": sorted(REDACTION_REASONS),
            },
        },
    }


def _main(argv: list[str] | None = None) -> int:
    """Print the receipt JSON Schema (or write it with ``--out``).

    This makes the schema export reachable from an installed wheel via
    ``python -m roam.evidence.mcp_receipt_schema`` — ``scripts/`` lives
    outside the ``roam`` package and is NOT shipped to PyPI, so a
    ``pip install roam-code`` user could not otherwise run the export.
    The companion ``scripts/export_mcp_receipt_schema.py`` is a thin
    in-repo delegator to this function.

    Output is JSON Schema Draft 2020-12 with sorted keys and 2-space
    indent — deterministic, so gateway integrators can diff a vendored
    copy across roam releases.
    """
    parser = argparse.ArgumentParser(
        prog="python -m roam.evidence.mcp_receipt_schema",
        description="Export the McpDecisionReceipt JSON Schema (Draft 2020-12).",
    )
    parser.add_argument(
        "--out",
        metavar="PATH",
        default=None,
        help="Write the schema to PATH instead of stdout.",
    )
    args = parser.parse_args(argv)

    schema = mcp_receipt_json_schema()
    # Deterministic: sorted keys + 2-space indent. Easy to diff in PRs.
    text = json.dumps(schema, indent=2, sort_keys=True) + "\n"

    if args.out is not None:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)
    return 0


__all__ = ["mcp_receipt_json_schema", "SCHEMA_ID", "SCHEMA_VERSION"]


if __name__ == "__main__":
    raise SystemExit(_main())
