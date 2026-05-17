"""Wave B3 (W767): shared ``_SCHEMA_ORACLE`` for the 5 boolean-oracle wrappers.

These tests assert that all 5 oracle MCP wrappers
(``roam_oracle_symbol_exists`` / ``_route_exists`` / ``_is_test_only`` /
``_is_reachable_from_entry`` / ``_is_clone_of``) plus the
``roam_oracle_test_only`` short-name alias emit a JSON envelope that
matches the shared ``_SCHEMA_ORACLE``. Wave B3 is the bundled-identity
sprint per ``(internal memo)``: all 5
oracles share the tri-state ``{verdict, value, reason, reason_class,
confidence}`` shape emitted by ``cmd_oracle._emit``, so one schema fits
all.

The structural validator is the same lightweight walker shipped in
Wave B1 (``test_mcp_outputschema_wave_b1.py``) -- required-keys check,
declared-type check, closed-enum check, ``minimum`` / ``maximum`` numeric
bounds. We don't require ``jsonschema`` at runtime.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.mcp_server import _SCHEMA_ORACLE

# ---------------------------------------------------------------------------
# Tiny structural validator (mirrors Wave B1 — keep in sync if extended)
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
# Test fixture — indexed mini-repo with a known symbol + test caller
# ---------------------------------------------------------------------------


def _make_indexed_repo(tmp: Path) -> None:
    """Init a git repo, write a tiny module with a symbol + test caller, run ``roam init``."""
    (tmp / "src").mkdir()
    (tmp / "src" / "app.py").write_text(
        "def core():\n"
        "    return 1\n"
        "\n"
        "def caller():\n"
        "    return core()\n",
        encoding="utf-8",
    )
    (tmp / "tests").mkdir()
    (tmp / "tests" / "test_app.py").write_text(
        "from app import core\n"
        "\n"
        "def test_core():\n"
        "    assert core() == 1\n",
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


# (cli_args, command_name) — the 5 oracles + the alias share `_SCHEMA_ORACLE`.
# `command` field on the envelope echoes the CLI subcommand, NOT the MCP wrapper
# name, so the alias maps to the same `oracle:is-test-only` command field.
_ORACLE_CASES: list[tuple[list[str], str]] = [
    (["--json", "oracle", "symbol-exists", "core"], "oracle:symbol-exists"),
    (["--json", "oracle", "route-exists", "/api/users"], "oracle:route-exists"),
    (["--json", "oracle", "is-test-only", "core"], "oracle:is-test-only"),
    (
        ["--json", "oracle", "is-reachable-from-entry", "core", "--max-hops", "5"],
        "oracle:is-reachable-from-entry",
    ),
    (["--json", "oracle", "is-clone-of", "core"], "oracle:is-clone-of"),
]


@pytest.mark.parametrize(("cli_args", "expected_command"), _ORACLE_CASES)
def test_oracle_envelope_validates_against_schema(
    cli_args: list[str], expected_command: str, tmp_path: Path
) -> None:
    """Each of the 5 oracles emits a `_SCHEMA_ORACLE`-conformant envelope."""
    _make_indexed_repo(tmp_path)
    exit_code, envelope = _run_in(tmp_path, cli_args)
    assert exit_code == 0, envelope
    assert envelope["command"] == expected_command
    errors = _validate(envelope, _SCHEMA_ORACLE)
    assert not errors, "\n".join([f"Schema validation errors for {expected_command}:", *errors])


def test_oracle_schema_structure() -> None:
    """The shared schema declares the tri-state envelope keys + closed enums.

    All 5 oracles emit ``summary.value: bool|null`` (tri-state) +
    ``verdict`` / ``reason_class`` / ``confidence`` closed enums per
    ``cmd_oracle.OracleResult`` docstring. The schema MUST encode these
    or it fails to constrain the envelope at all.
    """
    props = _SCHEMA_ORACLE["properties"]
    summary_props = props["summary"]["properties"]
    # Closed-enum sanity: tri-state verdict.
    assert summary_props["verdict"]["enum"] == ["true", "false", "indeterminate"]
    # Closed-enum sanity: confidence taxonomy.
    assert summary_props["confidence"]["enum"] == [
        "high",
        "medium",
        "low",
        "indeterminate",
    ]
    # Closed-enum sanity: reason_class taxonomy (8 documented values).
    assert summary_props["reason_class"]["enum"] == [
        "definitive_yes",
        "definitive_no",
        "indeterminate_workspace",
        "indeterminate_no_data",
        "unreachable_dead",
        "unreachable_scaffolding",
        "unreachable_test_only",
        "unreachable_dynamic_import",
    ]
    # value: bool | null — tri-state allowance.
    assert summary_props["value"]["type"] == ["boolean", "null"]
    # Required summary fields: every oracle envelope MUST emit all 5.
    assert _SCHEMA_ORACLE["properties"]["summary"]["required"] == [
        "verdict",
        "value",
        "reason",
        "reason_class",
        "confidence",
    ]
    # Top-level required: command + summary.
    assert _SCHEMA_ORACLE["required"] == ["command", "summary"]


def test_oracle_wrappers_wired_with_shared_schema() -> None:
    """Confirm the 5 oracle MCP wrappers + 1 alias share ``_SCHEMA_ORACLE``.

    Reads ``_REGISTERED_TOOLS`` and verifies the ``output_schema``
    attribute on each oracle wrapper points at the W767 specialised
    schema. Skip when the registry shape doesn't expose schemas
    (matches the Wave B1 test pattern).
    """
    from roam.mcp_server import _REGISTERED_TOOLS
    from roam.mcp_server import _SCHEMA_ORACLE as expected_schema

    oracle_tool_names = {
        "roam_oracle_symbol_exists",
        "roam_oracle_route_exists",
        "roam_oracle_is_test_only",
        "roam_oracle_is_reachable_from_entry",
        "roam_oracle_is_clone_of",
        "roam_oracle_test_only",  # alias to is_test_only
    }
    found: dict = {}
    for entry in _REGISTERED_TOOLS:
        name = entry.get("name") if isinstance(entry, dict) else getattr(entry, "name", None)
        if name in oracle_tool_names:
            schema = (
                entry.get("output_schema")
                if isinstance(entry, dict)
                else getattr(entry, "output_schema", None)
            )
            found[name] = schema

    if not found:
        pytest.skip("_REGISTERED_TOOLS doesn't expose schemas in this introspection shape")

    # Whatever oracle entries are surfaced via introspection MUST point at
    # the shared schema -- no per-wrapper override.
    for name, schema in found.items():
        assert schema is expected_schema, (
            f"{name} output_schema is not _SCHEMA_ORACLE (bundled-identity drift)"
        )
