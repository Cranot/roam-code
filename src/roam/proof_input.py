"""Strict, shared input checks for proof producers and saved-proof readers.

This validates input representation, not evidence truth, freshness, or authority.
"""

from __future__ import annotations

import json
import math


def parse_proof_json(text: str) -> dict:
    """Decode UTF-8 text without duplicate-key or non-finite-number ambiguity."""
    parsed = json.loads(
        text,
        object_pairs_hook=_unique_object,
        parse_constant=_invalid_constant,
        parse_float=_finite_float,
    )
    validate_proof_input(parsed)
    return parsed


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    """Refuse ambiguous JSON before last-key-wins can erase a blocker."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("bundle contains a duplicate JSON object key")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError("bundle numbers must be finite JSON numbers")


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        _invalid_constant(value)
    return parsed


def validate_proof_input(bundle: object) -> None:
    """Validate consumed field types without requiring modern bundle metadata.

    Missing/null optional fields retain legacy meaning. Present wrong-shaped
    evidence is not absence: refuse it before the tolerant mapper drops it.
    This is input validation, not signature, freshness, or policy verification.
    """
    if not isinstance(bundle, dict):
        raise ValueError("bundle must be a JSON object")
    for key in ("body", "bundle"):
        if bundle.get(key) is not None:
            validate_proof_input(bundle[key])
    mapping_keys = (
        "verification_contract",
        "review_evidence",
        "orchestration_contract",
        "risk",
        "risks_considered_block",
        "ledger",
    )
    for key in mapping_keys:
        if bundle.get(key) is not None and not isinstance(bundle[key], dict):
            raise ValueError(f"{key} must be an object or null")
    for key in ("change_set_unanalyzable",):
        if bundle.get(key) is not None and not isinstance(bundle[key], str):
            raise ValueError(f"{key} must be a string or null")
    _validate_record_lists(
        bundle,
        (
            "executed_checks",
            "missing_checks",
            "optimizer_findings",
            "scope_findings",
            "mcp_tool_findings",
            "tests_run",
        ),
    )
    _validate_contract_shape(bundle)
    _validate_evidence_state(bundle)


def _validate_contract_shape(bundle: dict) -> None:
    """Check contract containers before reading obligations or path metadata."""
    contract = bundle.get("verification_contract") or {}
    if "required" in contract and not isinstance(contract["required"], list):
        raise ValueError("verification_contract.required must be a list")
    _validate_record_lists(contract, ("required",))
    orchestration = bundle.get("orchestration_contract") or {}
    if "obligations" in orchestration and not isinstance(orchestration["obligations"], list):
        raise ValueError("orchestration_contract.obligations must be a list")
    meta = contract.get("_meta")
    if meta is not None:
        if not isinstance(meta, dict):
            raise ValueError("verification_contract._meta must be an object")
        if meta.get("rule_pack") is not None and not isinstance(meta["rule_pack"], dict):
            raise ValueError("verification_contract._meta.rule_pack must be an object")
    for record in (bundle, meta or {}, bundle.get("risk") or {}, bundle.get("risks_considered_block") or {}):
        _validate_path_lists(record)


def _validate_path_lists(record: dict) -> None:
    for key in ("changed_files", "unmatched_changed_files", "paths"):
        value = record.get(key)
        if value is not None and (not isinstance(value, list) or any(not isinstance(item, str) for item in value)):
            raise ValueError(f"{key} must be a list of strings")


def _validate_evidence_state(bundle: dict) -> None:
    """Refuse malformed state without changing which valid statuses block."""
    verified = (bundle.get("ledger") or {}).get("verified")
    if verified is not None and not isinstance(verified, bool):
        raise ValueError("ledger.verified must be a boolean or null")
    for result in (bundle.get("review_evidence") or {}).values():
        if result is not None and not isinstance(result, dict):
            raise ValueError("review_evidence entries must be objects or null")
        if isinstance(result, dict) and "status" in result and not isinstance(result["status"], str):
            raise ValueError("review_evidence status must be a string")


def _validate_record_lists(source: dict, keys: tuple[str, ...]) -> None:
    for key in keys:
        records = source.get(key)
        if records is None:
            continue
        if not isinstance(records, list) or any(not isinstance(record, dict) for record in records):
            raise ValueError(f"{key} must be a list of objects or null")
        for record in records:
            _validate_check_record(record, key)


def _validate_check_record(record: dict, key: str) -> None:
    if key in ("required", "executed_checks", "tests_run"):
        command = record.get("command")
        if key == "tests_run":
            command = command or record.get("name") or record.get("test")
        if not isinstance(command, str) or not command.strip():
            raise ValueError(f"{key} records must name a non-empty command")
    fields = ("command", "status", "name", "test", "result") if key == "tests_run" else ("command", "status")
    for field in fields:
        if record.get(field) is not None and not isinstance(record[field], str):
            raise ValueError(f"{key}.{field} must be a string or null")
