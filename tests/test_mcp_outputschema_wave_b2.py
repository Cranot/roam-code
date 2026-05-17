"""Wave B2 (W767): outputSchema specialisation for ``roam_health`` + ``roam_understand``.

These tests assert that the per-command JSON Schemas declared on the two
flagship comprehension/quality MCP wrappers structurally match the JSON
envelope each underlying CLI command actually emits. Mirrors the Wave B1
test layout in ``tests/test_mcp_outputschema_wave_b1.py`` — a tiny
draft-07-shaped structural walker, no ``jsonschema`` runtime dep.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.mcp_server import _SCHEMA_HEALTH, _SCHEMA_UNDERSTAND

# ---------------------------------------------------------------------------
# Tiny structural validator (covers the subset we actually use)
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
    """Init a git repo, write a tiny multi-symbol module, run ``roam init``."""
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


def test_health_envelope_validates_against_schema(tmp_path: Path) -> None:
    """Run ``roam --json health`` and validate against ``_SCHEMA_HEALTH``."""
    _make_indexed_repo(tmp_path)
    exit_code, envelope = _run_in(tmp_path, ["--json", "health"])
    assert exit_code == 0, envelope
    assert envelope["command"] == "health"
    errors = _validate(envelope, _SCHEMA_HEALTH)
    assert not errors, "\n".join(["Schema validation errors:", *errors])


def test_understand_envelope_validates_against_schema(tmp_path: Path) -> None:
    """Run ``roam --json understand`` and validate against ``_SCHEMA_UNDERSTAND``."""
    _make_indexed_repo(tmp_path)
    exit_code, envelope = _run_in(tmp_path, ["--json", "understand"])
    assert exit_code == 0, envelope
    assert envelope["command"] == "understand"
    errors = _validate(envelope, _SCHEMA_UNDERSTAND)
    assert not errors, "\n".join(["Schema validation errors:", *errors])


def test_health_schema_structure() -> None:
    """The specialised schema declares the high-leverage envelope keys agents read."""
    props = _SCHEMA_HEALTH["properties"]
    summary_props = props["summary"]["properties"]
    # Summary axis — agent-actionable signals on the default scoring branch.
    for key in (
        "verdict",
        "health_score",
        "tangle_ratio",
        "propagation_cost",
        "algebraic_connectivity",
        "issue_count",
        "severity",
        "category_severity",
    ):
        assert key in summary_props, f"_SCHEMA_HEALTH.summary missing {key!r}"
    # 4 issue-category fields on top-level.
    for key in ("cycles", "god_components", "bottlenecks", "layer_violations"):
        assert key in props, f"_SCHEMA_HEALTH missing top-level {key!r}"
    # Empty-corpus state (W834) is a closed enum.
    assert summary_props["state"]["enum"] == ["empty_corpus"]
    # Health score is bounded 0..100 (skipped on empty-corpus where it's None).
    assert summary_props["health_score"]["minimum"] == 0
    assert summary_props["health_score"]["maximum"] == 100


def test_understand_schema_structure() -> None:
    """The specialised schema covers the architecture + comprehension fields."""
    props = _SCHEMA_UNDERSTAND["properties"]
    summary_props = props["summary"]["properties"]
    # Summary axis.
    for key in (
        "verdict",
        "health_score",
        "files",
        "symbols",
        "languages",
        "caller_metric_definition",
    ):
        assert key in summary_props, f"_SCHEMA_UNDERSTAND.summary missing {key!r}"
    # Top-level briefing axis — what makes ``understand`` an exploration-first call.
    for key in (
        "project",
        "tech_stack",
        "architecture",
        "health_summary",
        "hotspots",
        "next_steps",
    ):
        assert key in props, f"_SCHEMA_UNDERSTAND missing top-level {key!r}"
    # architecture sub-shape covers layers / clusters / entry_points / key_abstractions.
    arch_props = props["architecture"]["properties"]
    for key in ("layers", "clusters", "entry_points", "key_abstractions"):
        assert key in arch_props, f"_SCHEMA_UNDERSTAND.architecture missing {key!r}"


def test_health_schema_required_summary_narrow_for_3_exit_paths() -> None:
    """W1238 / Pattern 1-variant-D: ``health`` has 3 distinct emit paths.

    - Default scoring (cmd_health.py:1795) emits health_score + tangle_ratio + ...
    - Baseline-mode (cmd_health.py:992, 1027) emits health_score + baseline_ref.
    - Empty-corpus (cmd_health.py:1180, W834) emits health_score=None + state="empty_corpus".

    Only ``verdict`` is universal — narrow ``required`` protects the
    variant-D envelope from schema lies.
    """
    summary_schema = _SCHEMA_HEALTH["properties"]["summary"]
    assert summary_schema["required"] == ["verdict"]


def test_understand_schema_required_summary_narrow_for_sub_modes() -> None:
    """``understand`` has 3 sub-modes (default / --agent-prompt / --skeleton).

    The --skeleton mode's no-symbols branch (cmd_understand.py:1199-1212)
    emits a different set of summary keys (file_count + symbol_count, no
    health_score). Keep ``required`` narrow.
    """
    summary_schema = _SCHEMA_UNDERSTAND["properties"]["summary"]
    assert summary_schema["required"] == ["verdict"]


def test_health_and_understand_wired_in_decorators() -> None:
    """Confirm the @_tool decorators carry the specialised schemas (no drift).

    Reads `_REGISTERED_TOOLS` and verifies the output_schema attribute on
    the health + understand wrappers points at the W767 specialised
    schema. Skips if the registry shape doesn't expose schemas as raw
    dicts (envelope-validation tests above already prove end-to-end).
    """
    from roam.mcp_server import _REGISTERED_TOOLS

    found = {}
    for entry in _REGISTERED_TOOLS:
        name = entry.get("name") if isinstance(entry, dict) else getattr(entry, "name", None)
        if name in {"roam_health", "roam_understand"}:
            schema = entry.get("output_schema") if isinstance(entry, dict) else getattr(entry, "output_schema", None)
            found[name] = schema

    if not found:
        pytest.skip("_REGISTERED_TOOLS doesn't expose schemas in this introspection shape")

    if "roam_health" in found:
        assert found["roam_health"] is _SCHEMA_HEALTH
    if "roam_understand" in found:
        assert found["roam_understand"] is _SCHEMA_UNDERSTAND
