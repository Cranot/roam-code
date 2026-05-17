"""Wave B5 (W767): outputSchema specialisation for ``roam_diagnose`` +
``roam_audit_trail_verify``.

Wave B5 is the audit + diagnostic sprint per
``(internal memo)``. This wave ships 2 of
the 5 originally-queued candidates; ``audit_trail_conformance_check``,
``fetch_handle``, and ``validate_plan`` are queued separately as Wave
B5b (richer per-command envelopes).

These tests assert that the per-command JSON Schemas declared on the two
flagship audit + diagnostic MCP wrappers structurally match the JSON
envelope each underlying CLI command actually emits. Same lightweight
validator as Wave B1-B3 (no ``jsonschema`` runtime dep).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.mcp_server import _SCHEMA_AUDIT_TRAIL_VERIFY, _SCHEMA_DIAGNOSE

# ---------------------------------------------------------------------------
# Tiny structural validator (mirrors Wave B1/B2/B3 — keep in sync if extended)
# ---------------------------------------------------------------------------


_PY_TYPE_TO_JSON = (
    (bool, "boolean"),  # MUST come before int (bool is a subclass of int).
    (int, "integer"),
    (float, "number"),
    (str, "string"),
    (list, "array"),
    (dict, "object"),
    (type(None), "null"),
)


def _json_type(value) -> str:
    for py_type, name in _PY_TYPE_TO_JSON:
        if isinstance(value, py_type):
            return name
    return "unknown"


def _validate(instance, schema, path: str = "") -> list[str]:
    """Return a list of validation errors; empty list means valid."""
    errors: list[str] = []

    expected_type = schema.get("type")
    if expected_type:
        allowed = expected_type if isinstance(expected_type, list) else [expected_type]
        actual = _json_type(instance)
        # int satisfies "number" per JSON Schema spec.
        if actual == "integer" and "number" in allowed and "integer" not in allowed:
            pass
        elif actual not in allowed:
            errors.append(f"{path or '<root>'}: type {actual!r} not in {allowed!r}")
            return errors

    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path or '<root>'}: {instance!r} not in enum {schema['enum']!r}")

    if isinstance(instance, dict):
        for required_key in schema.get("required", []):
            if required_key not in instance:
                errors.append(f"{path or '<root>'}: missing required key {required_key!r}")
        props = schema.get("properties", {})
        for key, sub_schema in props.items():
            if key in instance:
                errors.extend(_validate(instance[key], sub_schema, f"{path}.{key}" if path else key))

    if isinstance(instance, list):
        item_schema = schema.get("items")
        if item_schema:
            for idx, item in enumerate(instance):
                errors.extend(_validate(item, item_schema, f"{path}[{idx}]"))

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path or '<root>'}: {instance!r} < minimum {schema['minimum']!r}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path or '<root>'}: {instance!r} > maximum {schema['maximum']!r}")

    return errors


# ---------------------------------------------------------------------------
# Test fixtures — indexed mini-repo
# ---------------------------------------------------------------------------


def _make_indexed_repo(tmp: Path) -> None:
    """Init a git repo, write a tiny multi-symbol module + caller, run ``roam init``."""
    (tmp / "src").mkdir()
    (tmp / "src" / "app.py").write_text(
        "def core():\n"
        "    return 1\n"
        "\n"
        "def caller_a():\n"
        "    return core()\n"
        "\n"
        "def caller_b():\n"
        "    return core() + caller_a()\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "."],
        cwd=tmp,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-m",
            "init",
            "-q",
        ],
        cwd=tmp,
        check=True,
    )
    runner = CliRunner()
    cwd = os.getcwd()
    os.chdir(tmp)
    try:
        result = runner.invoke(cli, ["init"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
    finally:
        os.chdir(cwd)


def _run_in(tmp: Path, args: list[str]) -> tuple[int, dict]:
    """Run ``roam <args>`` with CWD=tmp; return (exit_code, parsed envelope)."""
    runner = CliRunner()
    cwd = os.getcwd()
    os.chdir(tmp)
    try:
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(cwd)
    payload = json.loads(result.output) if result.output.strip() else {}
    return result.exit_code, payload


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_diagnose_envelope_validates_against_schema(tmp_path: Path) -> None:
    """Run ``roam --json diagnose core`` and validate against ``_SCHEMA_DIAGNOSE``."""
    _make_indexed_repo(tmp_path)
    exit_code, envelope = _run_in(tmp_path, ["--json", "diagnose", "core"])
    assert exit_code == 0, envelope
    assert envelope["command"] == "diagnose"
    errors = _validate(envelope, _SCHEMA_DIAGNOSE)
    assert not errors, "\n".join(["Schema validation errors:", *errors])


def test_audit_trail_verify_uninitialized_envelope_validates(tmp_path: Path) -> None:
    """Verify a missing audit trail (uninitialized state) -- the easiest 3-state branch."""
    _make_indexed_repo(tmp_path)
    # No --gate, so uninitialized state exits 0 (still emits the envelope).
    exit_code, envelope = _run_in(tmp_path, ["--json", "audit-trail-verify"])
    assert exit_code == 0, envelope
    assert envelope["command"] == "audit-trail-verify"
    # Uninitialized: no audit trail file exists in tmp_path.
    assert envelope["summary"]["state"] == "uninitialized"
    assert envelope["summary"]["partial_success"] is True
    errors = _validate(envelope, _SCHEMA_AUDIT_TRAIL_VERIFY)
    assert not errors, "\n".join(["Schema validation errors:", *errors])


def test_audit_trail_verify_valid_envelope_validates(tmp_path: Path) -> None:
    """Seed a 1-record genesis trail; verify the ``valid`` 3-state branch."""
    _make_indexed_repo(tmp_path)
    # Write a genesis record (previous_record_hash="") -- the chain-walker
    # accepts this as a valid 1-record trail.
    trail_dir = tmp_path / ".roam"
    trail_dir.mkdir(exist_ok=True)
    trail_path = trail_dir / "audit-trail.jsonl"
    record = {
        "timestamp": "2026-05-16T00:00:00+00:00",
        "actor": "test-fixture",
        "verdict": "OK",
        "previous_record_hash": "",
        "diff_sha256": "abc",
        "git_sha": "def",
        "tool_version": "0.0.0",
    }
    trail_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    exit_code, envelope = _run_in(tmp_path, ["--json", "audit-trail-verify"])
    assert exit_code == 0, envelope
    assert envelope["command"] == "audit-trail-verify"
    assert envelope["summary"]["state"] == "valid"
    assert envelope["summary"]["chain_valid"] is True
    assert envelope["summary"]["total_records"] == 1
    errors = _validate(envelope, _SCHEMA_AUDIT_TRAIL_VERIFY)
    assert not errors, "\n".join(["Schema validation errors:", *errors])


def test_diagnose_schema_structure() -> None:
    """The specialised schema declares the suspect-ranking + cochange fields."""
    props = _SCHEMA_DIAGNOSE["properties"]
    summary_props = props["summary"]["properties"]
    # Summary axis -- agent-actionable signals on the success path.
    for key in (
        "verdict",
        "target",
        "upstream_count",
        "downstream_count",
        "caller_metric_definition",
        "complexity_definition",
        "resolution",
        "partial_success",
    ):
        assert key in summary_props, f"_SCHEMA_DIAGNOSE.summary missing {key!r}"
    # Top-level suspect-ranking axes -- the agent's primary read.
    for key in (
        "target_metrics",
        "upstream",
        "downstream",
        "cochange_partners",
        "recent_commits",
        "did_you_mean",
    ):
        assert key in props, f"_SCHEMA_DIAGNOSE missing top-level {key!r}"
    # resolution disclosure -- the closed enum prevents Pattern 2 variant-D drift.
    assert props["resolution"]["enum"] == ["symbol", "file", "unresolved", "fuzzy"]
    # not_found summary.state is the only closed-enum value (single-failure
    # branch); other states (success / partial) skip the field.
    assert summary_props["state"]["enum"] == ["not_found"]
    # Required summary is narrow -- verdict only -- to permit the
    # not_found branch which omits target/upstream/downstream counts.
    assert props["summary"]["required"] == ["verdict"]
    # Direction is a closed enum on suspect rows.
    upstream_item = props["upstream"]["items"]
    assert upstream_item["properties"]["direction"]["enum"] == ["upstream", "downstream"]
    # Required suspect-item field is narrow -- name only.
    assert upstream_item["required"] == ["name"]


def test_audit_trail_verify_schema_structure() -> None:
    """The specialised schema encodes the 3-state machine + issues[] shape."""
    props = _SCHEMA_AUDIT_TRAIL_VERIFY["properties"]
    summary_props = props["summary"]["properties"]
    # 3-state machine -- valid / broken / uninitialized is the load-bearing
    # closed enum (drives both the CI gate and downstream evidence projections).
    assert summary_props["state"]["enum"] == ["valid", "broken", "uninitialized"]
    # Summary axis -- agent-actionable signals.
    for key in (
        "verdict",
        "state",
        "partial_success",
        "chain_valid",
        "total_records",
        "issues_count",
        "audit_trail_path",
    ):
        assert key in summary_props, f"_SCHEMA_AUDIT_TRAIL_VERIFY.summary missing {key!r}"
    # Required is narrow -- verdict + state only -- because the 3 branches
    # have different optional fields populated (uninitialized omits
    # first_timestamp/last_timestamp/first_actor, etc).
    assert props["summary"]["required"] == ["verdict", "state"]
    # records is INT (count), not the records themselves -- this is the
    # envelope-construction parity flag at cmd_audit_trail_verify.py:346.
    assert props["records"]["type"] == "integer"
    # issues[] shape -- line + issue are required per-item per
    # cmd_audit_trail_verify._verify_chain construction at line 187-208.
    issues_item = props["issues"]["items"]
    assert issues_item["required"] == ["line", "issue"]


def test_diagnose_and_audit_trail_verify_wired_in_decorators() -> None:
    """Confirm the @_tool decorators carry the specialised schemas (no drift).

    Reads ``_REGISTERED_TOOLS`` and verifies the output_schema attribute on
    the diagnose + audit_trail_verify wrappers points at the W767 specialised
    schemas. Skips if the registry shape doesn't expose schemas as raw dicts
    -- mirrors the Wave B1/B2/B3 pattern (envelope-validation tests above
    already prove end-to-end).
    """
    from roam.mcp_server import _REGISTERED_TOOLS

    found: dict = {}
    targets = {"roam_diagnose", "roam_audit_trail_verify"}
    for entry in _REGISTERED_TOOLS:
        name = entry.get("name") if isinstance(entry, dict) else getattr(entry, "name", None)
        if name in targets:
            schema = entry.get("output_schema") if isinstance(entry, dict) else getattr(entry, "output_schema", None)
            found[name] = schema

    if not found:
        pytest.skip("_REGISTERED_TOOLS doesn't expose schemas in this introspection shape")

    if "roam_diagnose" in found:
        assert found["roam_diagnose"] is _SCHEMA_DIAGNOSE, (
            "roam_diagnose output_schema is not _SCHEMA_DIAGNOSE (Wave B5 drift)"
        )
    if "roam_audit_trail_verify" in found:
        assert found["roam_audit_trail_verify"] is _SCHEMA_AUDIT_TRAIL_VERIFY, (
            "roam_audit_trail_verify output_schema is not _SCHEMA_AUDIT_TRAIL_VERIFY (Wave B5 drift)"
        )
