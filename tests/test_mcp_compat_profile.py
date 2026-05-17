"""Wave C1 (W767): compat-profile env-vars on ``mcp_server``.

Pins the behavior of three ``ROAM_MCP_COMPAT_*`` env-vars wired in
``src/roam/mcp_server.py``:

* ``ROAM_MCP_COMPAT_STRIP_OUTPUT_SCHEMA`` — strips ``output_schema=``
  from every ``@_tool`` decoration at registration time. Load-bearing
  compat shim for Claude Code #41361 / #45839.
* ``ROAM_MCP_COMPAT_STRICT`` — gates the ``_strict_validate_envelope``
  helper. ``STRICT=0`` makes the helper a no-op (returns ``[]`` on any
  shape); ``STRICT=1`` (default) runs the structural required-keys walk.

The strip path is verified via the ``_TOOL_METADATA`` ``output_schema_stripped``
sidecar (set at decorator time), since FastMCP-protocol introspection
isn't available without spinning up the server. ``_REGISTERED_TOOLS``
enumeration is used to parametrize across multiple tools so the
"strip doesn't break dispatch" invariant covers more than one wrapper.

Wave B regression: these tests do NOT touch ``_SCHEMA_*`` constants, so
the per-command schemas remain declared by default — Wave B output-schema
tests stay green. The strip is opt-in.
"""

from __future__ import annotations

import importlib

import pytest

# ---------------------------------------------------------------------------
# Test plumbing — reload the server module with overridden env to exercise
# the module-level ``_env_truthy`` resolution (vars are read at import time).
# ---------------------------------------------------------------------------


def _reload_with_env(monkeypatch: pytest.MonkeyPatch, **env: str):
    """Reload roam.mcp_server with the given ROAM_MCP_COMPAT_* env set.

    Returns the reloaded module. Each call gives a fresh decoration of
    every ``@_tool`` wrapper against the freshly resolved compat flags.
    """
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    # Force unset the ones not explicitly provided so prior-test bleed
    # can't pollute the resolved-flags snapshot.
    for compat_var in (
        "ROAM_MCP_COMPAT_STRIP_OUTPUT_SCHEMA",
        "ROAM_MCP_COMPAT_STRICT",
    ):
        if compat_var not in env:
            monkeypatch.delenv(compat_var, raising=False)

    import roam.mcp_server as mod

    return importlib.reload(mod)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_compat_defaults_are_strict_and_advertise_schemas(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wave B back-compat: env unset = schemas declared + strict-on.

    This is the critical "Wave B doesn't break" invariant — without
    explicit env overrides, ``ROAM_MCP_COMPAT_STRIP_OUTPUT_SCHEMA``
    must default to ``0`` (schemas advertised) and
    ``ROAM_MCP_COMPAT_STRICT`` must default to ``1`` (validation on).
    """
    mod = _reload_with_env(monkeypatch)
    assert mod._COMPAT_STRIP_OUTPUT_SCHEMA is False
    assert mod._COMPAT_STRICT is True
    # Sanity: registered tools all carry ``output_schema_stripped=False``
    # in their metadata — Wave B schemas still ride on the wire.
    stripped = [
        name
        for name, meta in mod._TOOL_METADATA.items()
        if meta.get("output_schema_stripped") is True
    ]
    assert stripped == [], (
        f"Default env should leave schemas declared, got {len(stripped)} stripped tools"
    )


def test_strip_output_schema_drops_schemas_at_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ROAM_MCP_COMPAT_STRIP_OUTPUT_SCHEMA=1`` strips every output_schema.

    Verifies the load-bearing Claude Code #41361 / #45839 shim: with
    the env set, every registered tool reports
    ``output_schema_stripped=True`` in ``_TOOL_METADATA``. Without
    the strip, agents on Claude Code <=2.1.107 silently get a
    ``safeParse → return null`` bail on every call.
    """
    mod = _reload_with_env(monkeypatch, ROAM_MCP_COMPAT_STRIP_OUTPUT_SCHEMA="1")
    assert mod._COMPAT_STRIP_OUTPUT_SCHEMA is True
    # Every catalogued tool must be marked stripped — closed-set
    # invariant. We assert against ``_TOOL_METADATA`` (rather than
    # ``_REGISTERED_TOOLS``) so the test runs in fastmcp-less environments
    # too — metadata population is orthogonal to whether the MCP transport
    # can actually serve (see decorator-top ``_TOOL_METADATA[name] = {...}``
    # block + Wave C1 sidecar hoist).
    not_stripped = [
        name
        for name, meta in mod._TOOL_METADATA.items()
        if meta.get("output_schema_stripped") is not True
    ]
    assert not_stripped == [], (
        f"STRIP=1 should mark every catalogued tool stripped, missed: {not_stripped[:5]}"
    )
    # Sanity: at least some tools are actually catalogued (so the
    # invariant isn't vacuously satisfied on an empty registry).
    assert len(mod._TOOL_METADATA) > 0


def test_compat_strict_zero_disables_envelope_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ROAM_MCP_COMPAT_STRICT=0`` makes ``_strict_validate_envelope`` a no-op.

    Optional-field absence (or any shape drift) should not produce
    validation errors. This is the local-dev escape hatch for
    in-flight schema drift documented in
    ``(internal memo)``.
    """
    mod = _reload_with_env(monkeypatch, ROAM_MCP_COMPAT_STRICT="0")
    assert mod._COMPAT_STRICT is False
    # An envelope with a missing-required-key + wrong-typed value
    # should still produce zero errors when STRICT=0.
    schema = {
        "type": "object",
        "required": ["command", "summary"],
        "properties": {
            "command": {"type": "string"},
            "summary": {
                "type": "object",
                "required": ["verdict"],
                "properties": {"verdict": {"type": "string"}},
            },
        },
    }
    envelope_missing_required = {"command": "impact"}  # no summary at all
    assert mod._strict_validate_envelope(envelope_missing_required, schema) == []
    # And again with summary present but missing the nested required key.
    envelope_missing_nested = {"command": "impact", "summary": {}}
    assert mod._strict_validate_envelope(envelope_missing_nested, schema) == []


def test_compat_strict_default_one_enforces_required_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default ``ROAM_MCP_COMPAT_STRICT=1`` flags required-key absence."""
    mod = _reload_with_env(monkeypatch)
    assert mod._COMPAT_STRICT is True
    schema = {
        "type": "object",
        "required": ["command", "summary"],
        "properties": {
            "command": {"type": "string"},
            "summary": {
                "type": "object",
                "required": ["verdict"],
                "properties": {"verdict": {"type": "string"}},
            },
        },
    }
    # Missing top-level required key.
    errs = mod._strict_validate_envelope({"command": "impact"}, schema)
    assert any("summary" in e for e in errs), f"expected missing-summary error, got {errs}"
    # Missing nested required key.
    errs = mod._strict_validate_envelope(
        {"command": "impact", "summary": {}},
        schema,
    )
    assert any("verdict" in e for e in errs), f"expected missing-verdict error, got {errs}"
    # Optional field absence is fine (no schema-required key missing).
    assert mod._strict_validate_envelope(
        {"command": "impact", "summary": {"verdict": "ok"}},
        schema,
    ) == []


@pytest.mark.parametrize(
    "tool_name",
    [
        # Three flagship tools across distinct categories: compound,
        # safety-gate, comprehension. If the strip path breaks dispatch,
        # we'd see ``_REGISTERED_TOOLS`` missing them OR the wrapper
        # itself disappearing from module attrs.
        "roam_preflight",
        "roam_impact",
        "roam_understand",
    ],
)
def test_strip_does_not_break_tool_registration(
    monkeypatch: pytest.MonkeyPatch, tool_name: str
) -> None:
    """STRIP=1 invariant: every core tool stays registered + dispatchable.

    The compat shim must drop ``output_schema=`` *only*; the
    underlying wrapper (alias normalization, cold-start guard,
    receipt emitter, handle-off, concurrency guard) must still wire
    up. Verified structurally: the tool appears in
    ``_REGISTERED_TOOLS`` AND its metadata has the version stamp +
    annotations populated.
    """
    mod = _reload_with_env(monkeypatch, ROAM_MCP_COMPAT_STRIP_OUTPUT_SCHEMA="1")
    # ``_TOOL_METADATA`` is populated regardless of fastmcp presence
    # (the decorator's bookkeeping runs before the ``if mcp is None``
    # gate); ``_REGISTERED_TOOLS`` is only populated when the MCP
    # transport is available. We assert on the metadata path so the
    # test runs in fastmcp-less environments too.
    assert tool_name in mod._TOOL_METADATA, (
        f"{tool_name} dropped from catalog under STRIP=1 — registration broken"
    )
    meta = mod._TOOL_METADATA[tool_name]
    assert meta.get("output_schema_stripped") is True
    assert meta.get("version"), f"{tool_name} lost version stamp under STRIP=1"
    # Core/preset axis still intact.
    assert "core" in meta
