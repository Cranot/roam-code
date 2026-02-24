"""Schema registry for roam JSON envelope versioning."""

from __future__ import annotations

ENVELOPE_SCHEMA = {
    "name": "roam-envelope-v1",
    "version": "1.1.0",
    "description": "Standard JSON output envelope for all roam commands",
    "required_fields": {
        "schema": "Schema identifier string",
        "schema_version": "Semantic version of the envelope format",
        "command": "The roam command that produced this output",
        "version": "The roam CLI version",
        "summary": "Dict containing at minimum a 'verdict' string",
    },
    "optional_fields": {
        "_meta": "Non-deterministic metadata (timestamp, index_age_s) — separated for LLM cache stability",
        "Any additional keys": "Command-specific data fields",
    },
    "changelog": [
        {"version": "1.0.0", "date": "2026-02-20", "changes": ["Initial schema version"]},
        {"version": "1.1.0", "date": "2026-02-22", "changes": [
            "Moved timestamp and index_age_s to _meta sub-dict for deterministic output",
            "Added sort_keys=True to JSON serialization for LLM prompt-caching compatibility",
        ]},
    ],
}


def get_schema_info() -> dict:
    """Return the current schema definition."""
    return ENVELOPE_SCHEMA.copy()


def validate_envelope(data: dict) -> tuple[bool, list[str]]:
    """Validate a dict against the envelope schema.

    Returns (is_valid, list_of_errors).
    """
    errors = []
    for field in ENVELOPE_SCHEMA["required_fields"]:
        if field not in data:
            errors.append(f"Missing required field: {field}")

    if "summary" in data and not isinstance(data["summary"], dict):
        errors.append("'summary' must be a dict")

    if "schema_version" in data:
        parts = data["schema_version"].split(".")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            errors.append("'schema_version' must be semantic version (X.Y.Z)")

    return (len(errors) == 0, errors)
