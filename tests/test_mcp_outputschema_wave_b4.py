"""Wave B4 (W767): outputSchema specialisation for ``roam_timeline`` + ``roam_test_impact``.

Closes the W1312 QUEUE for the 2 deferred ``_ENVELOPE_SCHEMA``-literal
tools per ``(internal memo)``. Both
envelopes carried rich agent-actionable shape worth a real
``_make_schema(...)`` shape (timeline: 7 summary fields + commits[] +
authors{}; test_impact: agent post-change "which tests to run" with
the ``tests[{file, reach_count}]`` ranked-by-reach contract).

Mirrors the Wave B1/B2/B3 layout: tiny structural validator (in-sync
copy), indexed-mini-repo fixture, end-to-end envelope validation +
structural schema sanity + decorator-wiring check.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.mcp_server import _SCHEMA_TEST_IMPACT, _SCHEMA_TIMELINE

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

    # ``const`` constraint (used by ``command: {"const": "timeline"}``).
    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path or '<root>'}: {instance!r} != const {schema['const']!r}")

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
        # additionalProperties: validate each non-declared key against the schema.
        add_props = schema.get("additionalProperties")
        if isinstance(add_props, dict):
            for key, value in instance.items():
                if key not in props:
                    errors.extend(_validate(value, add_props, f"{path}.{key}" if path else key))

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
# Test fixture — indexed mini-repo with a known symbol + commit history
# ---------------------------------------------------------------------------


def _make_indexed_repo(tmp: Path) -> None:
    """Init a git repo, write a tiny module with a symbol + test caller,
    create 2 commits so timeline + test-impact have something to walk,
    then run ``roam init``.
    """
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
    # Second commit so timeline has more than one entry to render.
    (tmp / "src" / "app.py").write_text(
        "def core():\n"
        "    return 1\n"
        "\n"
        "def caller():\n"
        "    # tweak\n"
        "    return core()\n",
        encoding="utf-8",
    )
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
            "tweak",
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
# Tests — roam_timeline
# ---------------------------------------------------------------------------


def test_timeline_envelope_validates_against_schema(tmp_path: Path) -> None:
    """Run ``roam --json timeline <symbol>`` and validate against ``_SCHEMA_TIMELINE``."""
    _make_indexed_repo(tmp_path)
    exit_code, envelope = _run_in(tmp_path, ["--json", "timeline", "core"])
    assert exit_code == 0, envelope
    assert envelope["command"] == "timeline"
    errors = _validate(envelope, _SCHEMA_TIMELINE)
    assert not errors, "\n".join(["Schema validation errors:", *errors])
    # Sanity: the commits[] array should reflect the 2-commit fixture history.
    assert envelope["summary"]["commit_count"] >= 1
    assert "commits" in envelope and isinstance(envelope["commits"], list)
    assert "authors" in envelope and isinstance(envelope["authors"], dict)


def test_timeline_envelope_no_symbol_branch(tmp_path: Path) -> None:
    """The symbol-not-found branch emits a narrow envelope; schema still validates.

    ``cmd_timeline`` emits ``{summary: {verdict, commit_count: 0}, commits: []}``
    when the symbol can't be resolved -- ``file_path`` / ``top_author`` /
    etc. are absent. ``_SCHEMA_TIMELINE.summary.required`` is narrow on
    purpose so this branch validates without lying about the missing fields.
    """
    _make_indexed_repo(tmp_path)
    exit_code, envelope = _run_in(tmp_path, ["--json", "timeline", "no_such_symbol_xyz"])
    assert exit_code == 0, envelope
    assert envelope["command"] == "timeline"
    assert envelope["summary"]["commit_count"] == 0
    # Absent fields stay absent; required is only {verdict, commit_count}.
    assert "file_path" not in envelope["summary"]
    assert "top_author" not in envelope["summary"]
    errors = _validate(envelope, _SCHEMA_TIMELINE)
    assert not errors, "\n".join(["Schema validation errors:", *errors])


def test_timeline_schema_structure() -> None:
    """The specialised schema declares the 7-field summary + commits[] + authors{}."""
    props = _SCHEMA_TIMELINE["properties"]
    summary_props = props["summary"]["properties"]
    # Summary axis — every field the timeline envelope can carry.
    for key in (
        "verdict",
        "commit_count",
        "file_path",
        "added_total",
        "removed_total",
        "distinct_authors",
        "top_author",
    ):
        assert key in summary_props, f"_SCHEMA_TIMELINE.summary missing {key!r}"
    # ``command`` is a const literal -- only the canonical CLI subcommand validates.
    assert props["command"] == {"const": "timeline"}
    # ``top_author`` is nullable because the empty-commit-list path sets it to None.
    assert summary_props["top_author"]["type"] == ["string", "null"]
    # ``required`` is intentionally narrow (only the always-emitted fields).
    assert _SCHEMA_TIMELINE["properties"]["summary"]["required"] == [
        "verdict",
        "commit_count",
    ]
    # Top-level: command + summary required; commits[] + authors{} declared.
    assert _SCHEMA_TIMELINE["required"] == ["command", "summary"]
    assert "commits" in props
    assert "authors" in props
    # commits[].items declares the 6-field row contract.
    commit_item_props = props["commits"]["items"]["properties"]
    for key in ("sha", "date", "author", "added", "removed", "subject"):
        assert key in commit_item_props, f"_SCHEMA_TIMELINE.commits[].{key!r} missing"


# ---------------------------------------------------------------------------
# Tests — roam_test_impact
# ---------------------------------------------------------------------------


def test_test_impact_envelope_validates_against_schema(tmp_path: Path) -> None:
    """Run ``roam --json test-impact`` and validate against ``_SCHEMA_TEST_IMPACT``.

    Uses the working-tree branch (no commit_range arg) -- when there are
    no working-tree changes, the envelope still emits a structured
    no-changes verdict. Schema MUST validate that branch too.
    """
    _make_indexed_repo(tmp_path)
    exit_code, envelope = _run_in(tmp_path, ["--json", "test-impact"])
    assert exit_code == 0, envelope
    assert envelope["command"] == "test-impact"
    errors = _validate(envelope, _SCHEMA_TEST_IMPACT)
    assert not errors, "\n".join(["Schema validation errors:", *errors])
    # Both ``verdict`` and ``count`` are required and present on EVERY branch.
    assert "verdict" in envelope["summary"]
    assert "count" in envelope["summary"]
    assert envelope["summary"]["count"] >= 0


def test_test_impact_envelope_with_commit_range(tmp_path: Path) -> None:
    """``roam --json test-impact HEAD~1`` exercises the normal-walk branch."""
    _make_indexed_repo(tmp_path)
    exit_code, envelope = _run_in(tmp_path, ["--json", "test-impact", "HEAD~1"])
    assert exit_code == 0, envelope
    assert envelope["command"] == "test-impact"
    errors = _validate(envelope, _SCHEMA_TEST_IMPACT)
    assert not errors, "\n".join(["Schema validation errors:", *errors])
    # The HEAD~1 diff touches app.py; that's a non-test source file.
    assert "tests" in envelope and isinstance(envelope["tests"], list)
    # Items, when present, must each carry {file, reach_count} per schema.
    for item in envelope["tests"]:
        assert "file" in item and "reach_count" in item


def test_test_impact_schema_structure() -> None:
    """The specialised schema declares ``tests[]`` with required ``file`` + ``reach_count``."""
    props = _SCHEMA_TEST_IMPACT["properties"]
    summary_props = props["summary"]["properties"]
    # ``command`` is a const literal.
    assert props["command"] == {"const": "test-impact"}
    # Summary axis.
    assert "verdict" in summary_props
    assert "count" in summary_props
    assert summary_props["count"]["minimum"] == 0
    # ``required`` covers BOTH verdict + count (emitted on EVERY branch).
    assert _SCHEMA_TEST_IMPACT["properties"]["summary"]["required"] == ["verdict", "count"]
    # Top-level shape.
    assert _SCHEMA_TEST_IMPACT["required"] == ["command", "summary"]
    assert "changed_files" in props
    assert "tests" in props
    # tests[].items carries the strict {file, reach_count} contract.
    tests_item = props["tests"]["items"]
    assert tests_item["required"] == ["file", "reach_count"]
    assert tests_item["properties"]["reach_count"]["minimum"] == 1


# ---------------------------------------------------------------------------
# Wiring check — verify @_tool decorators carry the specialised schemas
# ---------------------------------------------------------------------------


def test_timeline_and_test_impact_wired_in_decorators() -> None:
    """Confirm the @_tool decorators carry the W1312-resolved schemas (no drift).

    Reads ``_REGISTERED_TOOLS`` and verifies the ``output_schema``
    attribute on the timeline + test_impact wrappers points at the W767
    Wave B4 specialised schemas. Skip when the registry shape doesn't
    expose schemas (matches the Wave B1/B2/B3 test pattern).
    """
    from roam.mcp_server import _REGISTERED_TOOLS

    expected_by_name = {
        "roam_timeline": _SCHEMA_TIMELINE,
        "roam_test_impact": _SCHEMA_TEST_IMPACT,
    }

    found: dict = {}
    for entry in _REGISTERED_TOOLS:
        name = entry.get("name") if isinstance(entry, dict) else getattr(entry, "name", None)
        if name in expected_by_name:
            schema = (
                entry.get("output_schema")
                if isinstance(entry, dict)
                else getattr(entry, "output_schema", None)
            )
            found[name] = schema

    if not found:
        pytest.skip("_REGISTERED_TOOLS doesn't expose schemas in this introspection shape")

    for name, schema in found.items():
        assert schema is expected_by_name[name], (
            f"{name} output_schema is not the W767 Wave B4 specialised schema"
        )
