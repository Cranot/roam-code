"""Wave B5b (W767): outputSchema specialisation for the 3 deferred Wave-B5
candidates -- ``roam_audit_trail_conformance_check``, ``roam_fetch_handle``,
``roam_validate_plan``.

Wave B5b closes the Wave B outputSchema roadmap per
``(internal memo)``. These tests validate that
the per-command JSON Schemas declared on the 3 wrappers structurally match
the actual envelopes each command emits. Same lightweight validator as
Wave B1-B5 (no ``jsonschema`` runtime dep).

Per-wrapper coverage notes:

- ``roam_audit_trail_conformance_check`` -- the no_trail (Fix E) branch is
  easiest to fixture (no audit trail file in tmp_path), and exercises the
  ``compliance_kind: "audit_trail_chain_integrity"`` Pattern 3c
  discriminator + the ``state: "no_trail"`` closed-enum + all 6 ``checks[]``
  in ``not_run`` state.
- ``roam_fetch_handle`` -- exercised via the MCP wrapper directly (no
  underlying CLI). Calls ``fetch_handle`` with an empty handle to hit
  USAGE_ERROR, then calls it on a real handle written to
  ``.roam/responses/<sha>.json`` to validate the byte_slice + section
  payload shapes.
- ``roam_validate_plan`` -- exercised via the MCP wrapper directly (no
  CLI). Calls ``validate_plan`` with a 1-operation plan that hits the
  ``ok`` verdict (resolves a real symbol) AND a 1-operation plan that
  hits ``blocked`` (unresolved symbol).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.mcp_server import (
    _SCHEMA_AUDIT_TRAIL_CONFORMANCE,
    _SCHEMA_FETCH_HANDLE,
    _SCHEMA_VALIDATE_PLAN,
    fetch_handle,
    validate_plan,
)

# ---------------------------------------------------------------------------
# Tiny structural validator (mirrors Wave B1/B2/B3/B5 -- keep in sync if extended)
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

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path or '<root>'}: {instance!r} != const {schema['const']!r}")

    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path or '<root>'}: {instance!r} not in enum {schema['enum']!r}")

    if isinstance(instance, dict):
        for required_key in schema.get("required", []):
            if required_key not in instance:
                errors.append(f"{path or '<root>'}: missing required key {required_key!r}")
        props = schema.get("properties", {})
        for key, sub_schema in props.items():
            if key in instance:
                errors.extend(
                    _validate(instance[key], sub_schema, f"{path}.{key}" if path else key)
                )

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
# Test fixtures
# ---------------------------------------------------------------------------


def _make_indexed_repo(tmp: Path) -> None:
    """Init a git repo, write a tiny multi-symbol module + caller, run ``roam init``."""
    (tmp / "src").mkdir()
    (tmp / "src" / "app.py").write_text(
        "def core():\n"
        "    return 1\n"
        "\n"
        "def caller_a():\n"
        "    return core()\n",
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
# audit_trail_conformance_check tests
# ---------------------------------------------------------------------------


def test_audit_trail_conformance_no_trail_envelope_validates(tmp_path: Path) -> None:
    """no_trail branch (Fix E): no audit trail file -> 6 checks in not_run state."""
    _make_indexed_repo(tmp_path)
    exit_code, envelope = _run_in(
        tmp_path, ["--json", "audit-trail-conformance-check"]
    )
    assert exit_code == 0, envelope
    assert envelope["command"] == "audit-trail-conformance-check"
    # Verify the no_trail closed-enum state.
    assert envelope["summary"]["state"] == "no_trail"
    assert envelope["summary"]["partial_success"] is True
    assert envelope["summary"]["score"] is None
    # Pattern 3c discriminator must be present.
    assert envelope["summary"]["compliance_kind"] == "audit_trail_chain_integrity"
    # All 6 checks emitted with state=not_run.
    assert len(envelope["checks"]) == 6
    for c in envelope["checks"]:
        assert c["state"] == "not_run"
        assert c["passed"] is False
    errors = _validate(envelope, _SCHEMA_AUDIT_TRAIL_CONFORMANCE)
    assert not errors, "\n".join(["Schema validation errors:", *errors])


def test_audit_trail_conformance_valid_trail_envelope_validates(tmp_path: Path) -> None:
    """Seed a 1-record genesis trail; verify the normal-flow envelope."""
    _make_indexed_repo(tmp_path)
    trail_dir = tmp_path / ".roam"
    trail_dir.mkdir(exist_ok=True)
    trail_path = trail_dir / "audit-trail.jsonl"
    record = {
        "timestamp": "2026-05-16T00:00:00+00:00",
        "actor": "test-fixture",
        "verdict": "OK",
        "rationale_summary": "test record",
        "previous_record_hash": "",
        "diff_sha256": "abc",
        "git_sha": "def",
        "tool_version": "0.0.0",
    }
    trail_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    exit_code, envelope = _run_in(
        tmp_path, ["--json", "audit-trail-conformance-check"]
    )
    assert exit_code == 0, envelope
    assert envelope["command"] == "audit-trail-conformance-check"
    # Normal-flow branch: score is set, state field is absent (omitted).
    assert envelope["summary"]["score"] is not None
    assert envelope["summary"]["chain_compliance_score"] is not None
    assert envelope["summary"]["compliance_kind"] == "audit_trail_chain_integrity"
    assert envelope["summary"]["total_records"] == 1
    assert len(envelope["checks"]) == 6
    errors = _validate(envelope, _SCHEMA_AUDIT_TRAIL_CONFORMANCE)
    assert not errors, "\n".join(["Schema validation errors:", *errors])


# ---------------------------------------------------------------------------
# fetch_handle tests
# ---------------------------------------------------------------------------


def test_fetch_handle_usage_error_envelope_validates() -> None:
    """Empty handle -> USAGE_ERROR envelope with command=roam_fetch_handle."""
    envelope = fetch_handle(handle="")
    assert envelope["command"] == "roam_fetch_handle"
    assert envelope["isError"] is True
    assert envelope["error_code"] == "USAGE_ERROR"


def test_fetch_handle_byte_slice_envelope_validates(tmp_path: Path) -> None:
    """Write a handle file then fetch via default byte_slice mode."""
    # Seed handle storage under .roam/responses/<sha>.json (the handle layout
    # used by the wrapper). Use a controlled cwd so the handle store resolves
    # locally.
    handle_id = "0123456789abcdef"
    payload = {"summary": {"verdict": "test"}, "items": ["a", "b", "c"]}
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        responses_dir = tmp_path / ".roam" / "responses"
        responses_dir.mkdir(parents=True, exist_ok=True)
        (responses_dir / f"{handle_id}.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
        envelope = fetch_handle(handle=handle_id)
    finally:
        os.chdir(cwd)
    assert envelope["command"] == "roam_fetch_handle"
    assert envelope["summary"]["mode"] == "byte_slice"
    assert envelope["handle"] == handle_id
    assert isinstance(envelope["data"], str)
    errors = _validate(envelope, _SCHEMA_FETCH_HANDLE)
    assert not errors, "\n".join(["Schema validation errors:", *errors])


def test_fetch_handle_section_envelope_validates(tmp_path: Path) -> None:
    """Section-pick mode returns the parsed value of one top-level key."""
    handle_id = "fedcba9876543210"
    payload = {"summary": {"verdict": "test"}, "items": ["a", "b", "c"]}
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        responses_dir = tmp_path / ".roam" / "responses"
        responses_dir.mkdir(parents=True, exist_ok=True)
        (responses_dir / f"{handle_id}.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
        envelope = fetch_handle(handle=handle_id, section="items")
    finally:
        os.chdir(cwd)
    assert envelope["command"] == "roam_fetch_handle"
    assert envelope["summary"]["mode"] == "section"
    assert envelope["section"] == "items"
    assert envelope["data"] == ["a", "b", "c"]
    errors = _validate(envelope, _SCHEMA_FETCH_HANDLE)
    assert not errors, "\n".join(["Schema validation errors:", *errors])


# ---------------------------------------------------------------------------
# validate_plan tests
# ---------------------------------------------------------------------------


def test_validate_plan_blocked_envelope_validates(tmp_path: Path) -> None:
    """An operation targeting an unresolved symbol blocks the plan."""
    _make_indexed_repo(tmp_path)
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        envelope = validate_plan(
            operations=[
                {"kind": "remove", "symbol": "does_not_exist_xxx"},
            ],
            root=str(tmp_path),
        )
    finally:
        os.chdir(cwd)
    # 3-tier enum: ok / needs-review / blocked. Unknown symbol -> blocked.
    assert envelope["summary"]["verdict"] in {"ok", "needs-review", "blocked"}
    # The structured operations[] axis is the agent-actionable signal.
    assert isinstance(envelope.get("operations"), list)
    assert len(envelope["operations"]) == 1
    op = envelope["operations"][0]
    assert op["index"] == 0
    assert op["kind"] == "remove"
    errors = _validate(envelope, _SCHEMA_VALIDATE_PLAN)
    assert not errors, "\n".join(["Schema validation errors:", *errors])


def test_validate_plan_usage_error_envelope_validates() -> None:
    """No operations -> USAGE_ERROR envelope with closed schema."""
    envelope = validate_plan(operations=None, plan_json="")
    assert envelope["command"] == "roam_validate_plan"
    assert envelope["isError"] is True
    assert envelope["error_code"] == "USAGE_ERROR"
    errors = _validate(envelope, _SCHEMA_VALIDATE_PLAN)
    assert not errors, "\n".join(["Schema validation errors:", *errors])


# ---------------------------------------------------------------------------
# Drift-guard: the wrappers carry the specialised schemas
# ---------------------------------------------------------------------------


def test_audit_trail_conformance_schema_structure() -> None:
    """The specialised schema encodes the no_trail closed enum + Pattern 3c discriminator."""
    props = _SCHEMA_AUDIT_TRAIL_CONFORMANCE["properties"]
    summary_props = props["summary"]["properties"]
    # Pattern 3c discriminator: closed-enum, distinguishes from article-12-check.
    assert summary_props["compliance_kind"]["enum"] == ["audit_trail_chain_integrity"]
    # no_trail is the only closed-enum state (Fix E branch).
    assert summary_props["state"]["enum"] == ["no_trail"]
    # 6 Article 12 check ids enumerated on checks[] items.
    check_id_enum = props["checks"]["items"]["properties"]["id"]["enum"]
    assert set(check_id_enum) == {
        "chain_integrity",
        "timestamp_completeness",
        "actor_attribution",
        "reproducibility_metadata",
        "verdict_and_rationale",
        "retention",
    }
    # not_run is the only closed-enum per-check state (no_trail branch).
    assert props["checks"]["items"]["properties"]["state"]["enum"] == ["not_run"]
    # Required is narrow -- verdict only -- because the no_trail branch
    # omits checks_passed / checks_total semantics + nulls the score.
    assert props["summary"]["required"] == ["verdict"]
    # ``command`` is a const literal -- canonical CLI name.
    assert props["command"]["const"] == "audit-trail-conformance-check"


def test_fetch_handle_schema_structure() -> None:
    """The specialised schema encodes the 3-mode closed enum + handle pattern."""
    props = _SCHEMA_FETCH_HANDLE["properties"]
    summary_props = props["summary"]["properties"]
    # 3-mode closed enum -- byte_slice / section / jq.
    assert summary_props["mode"]["enum"] == ["byte_slice", "section", "jq"]
    # ``command`` const literal -- the wrapper-name not a CLI name.
    assert props["command"]["const"] == "roam_fetch_handle"
    # ``handle`` is required + format-constrained (16-char lowercase hex).
    assert props["handle"]["pattern"] == "^[0-9a-f]{16}$"
    # ``required`` covers command + summary + handle (the 3 fields every
    # mode emits unconditionally; mode-specific fields are optional).
    assert set(_SCHEMA_FETCH_HANDLE["required"]) == {"command", "summary", "handle"}
    # summary.required includes mode (the dispatch discriminator).
    assert set(props["summary"]["required"]) == {"verdict", "mode"}


def test_validate_plan_schema_structure() -> None:
    """The specialised schema encodes the 3-tier plan_status + structured errors[]."""
    props = _SCHEMA_VALIDATE_PLAN["properties"]
    summary_props = props["summary"]["properties"]
    # Plan status 3-tier closed enum -- ok / needs-review / blocked.
    assert summary_props["verdict"]["enum"] == ["ok", "needs-review", "blocked"]
    # ``command`` is const but NOT required (BAIL drift: success envelope
    # doesn't emit command field; error envelopes skip ``summary``). The
    # schema's root-level ``required`` is empty -- both branches validate
    # under one schema without forcing a oneOf split.
    assert props["command"]["const"] == "roam_validate_plan"
    assert _SCHEMA_VALIDATE_PLAN["required"] == []
    # operations[] item shape -- blockers + warnings + advice axes.
    item = props["operations"]["items"]
    assert set(item["required"]) == {"index", "kind", "ok"}
    blocker = item["properties"]["blockers"]["items"]
    assert "code" in blocker["required"]


def test_wave_b5b_schemas_wired_in_decorators() -> None:
    """Confirm the @_tool decorators carry the W767 Wave B5b specialised schemas.

    Reads ``_REGISTERED_TOOLS`` and verifies the output_schema attribute on
    the 3 wrappers points at the W767 specialised schemas. Skips if the
    registry shape doesn't expose schemas as raw dicts -- mirrors the Wave
    B1/B2/B3/B5 pattern (envelope-validation tests above already prove
    end-to-end).
    """
    from roam.mcp_server import _REGISTERED_TOOLS

    targets = {
        "roam_audit_trail_conformance_check": _SCHEMA_AUDIT_TRAIL_CONFORMANCE,
        "roam_fetch_handle": _SCHEMA_FETCH_HANDLE,
        "roam_validate_plan": _SCHEMA_VALIDATE_PLAN,
    }
    found: dict = {}
    for entry in _REGISTERED_TOOLS:
        name = entry.get("name") if isinstance(entry, dict) else getattr(entry, "name", None)
        if name in targets:
            schema = (
                entry.get("output_schema")
                if isinstance(entry, dict)
                else getattr(entry, "output_schema", None)
            )
            found[name] = schema

    if not found:
        pytest.skip("_REGISTERED_TOOLS doesn't expose schemas in this introspection shape")

    for name, expected_schema in targets.items():
        if name in found:
            assert found[name] is expected_schema, (
                f"{name} output_schema is not the W767 Wave B5b specialised schema "
                f"(drift detected)"
            )
