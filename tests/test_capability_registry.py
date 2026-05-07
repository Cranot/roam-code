"""Tests for roam.capability — decorator-driven introspection.

Capability Registry — declarative manifest of public surface.; these tests guard the contract
that the registry stays consistent across imports and emits stable
YAML / JSON for downstream consumers.
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from roam.capability import REGISTRY, Capability, CapabilityRegistry, emit_yaml, roam_capability
from roam.commands.cmd_capabilities import capabilities_cmd


def test_decorator_registers_capability() -> None:
    CapabilityRegistry()

    @roam_capability(category="test", summary="A test cap")
    def my_cmd():  # noqa: ANN202
        return "ok"

    # Decorator stashes metadata on the function
    assert hasattr(my_cmd, "__roam_capability__")
    cap = my_cmd.__roam_capability__
    assert isinstance(cap, Capability)
    assert cap.category == "test"
    assert cap.summary == "A test cap"

    # Module-level REGISTRY also picked it up
    found = REGISTRY.get("my-cmd")
    assert found is not None
    assert found.category == "test"


def test_derive_name_strips_cmd_prefix() -> None:
    @roam_capability(category="test", summary="x")
    def cmd_my_thing():  # noqa: ANN202
        pass

    cap = cmd_my_thing.__roam_capability__
    assert cap.name == "my-thing"


def test_explicit_name_wins() -> None:
    @roam_capability(category="test", summary="x", name="custom-name")
    def something():  # noqa: ANN202
        pass

    assert REGISTRY.get("custom-name") is not None
    assert something.__roam_capability__.name == "custom-name"


def test_emit_yaml_round_trip() -> None:
    yaml_out = emit_yaml()
    # Sanity: contains the schema-version line and at least one capability
    assert "schema_version: 1" in yaml_out
    assert "capabilities:" in yaml_out
    # And produces a single trailing newline
    assert yaml_out.endswith("\n")


def test_capabilities_cli_yaml_output() -> None:
    runner = CliRunner()
    result = runner.invoke(capabilities_cmd, ["--emit", "yaml"])
    assert result.exit_code == 0, result.output
    assert "schema_version: 1" in result.output


def test_capabilities_cli_json_output() -> None:
    runner = CliRunner()
    result = runner.invoke(capabilities_cmd, ["--emit", "json"], obj={})
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["command"] == "capabilities"
    assert "capabilities" in data
    # Each capability has the basics
    for cap in data["capabilities"]:
        assert "name" in cap
        assert "category" in cap
        assert "summary" in cap


def test_capabilities_cli_text_output_is_human_readable() -> None:
    runner = CliRunner()
    result = runner.invoke(capabilities_cmd, ["--emit", "text"], obj={})
    assert result.exit_code == 0
    # Should contain at least the count line
    assert "registered capabilities" in result.output


def test_capabilities_cli_filters_by_category() -> None:
    runner = CliRunner()
    result = runner.invoke(capabilities_cmd, ["--emit", "json", "--category", "review"], obj={})
    assert result.exit_code == 0
    data = json.loads(result.output)
    for cap in data["capabilities"]:
        assert cap["category"] == "review"


def test_capabilities_cli_ai_safe_only() -> None:
    runner = CliRunner()
    result = runner.invoke(capabilities_cmd, ["--emit", "json", "--ai-safe-only"], obj={})
    assert result.exit_code == 0
    data = json.loads(result.output)
    for cap in data["capabilities"]:
        assert cap["ai_safe"] is True


def test_phase0_commands_register() -> None:
    """The 3 Phase 0 commands (permit, postmortem, article-12-check) decorate themselves."""
    # Force-import via the populator
    from roam.commands.cmd_capabilities import _populate_registry

    _populate_registry()
    assert REGISTRY.get("permit") is not None
    assert REGISTRY.get("postmortem") is not None
    assert REGISTRY.get("article-12-check") is not None
    # And they're all ai_safe + tagged phase0
    for name in ("permit", "postmortem", "article-12-check"):
        cap = REGISTRY.get(name)
        assert cap is not None
        assert cap.ai_safe is True
        assert "phase0" in cap.tags


def test_collision_raises() -> None:
    reg = CapabilityRegistry()
    cap1 = Capability(name="X", category="c", summary="s", module="mod-a")
    cap2 = Capability(name="X", category="c", summary="s", module="mod-b")
    reg.register(cap1)
    try:
        reg.register(cap2)
    except ValueError as e:
        assert "name collision" in str(e)
    else:
        raise AssertionError("expected collision error")
