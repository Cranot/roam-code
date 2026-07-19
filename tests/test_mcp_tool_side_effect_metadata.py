"""Every @_tool wrapper must declare side-effect metadata.

Asserts that _TOOL_METADATA contains a read_only, destructive, and
idempotent boolean for every registered MCP tool.  Fails with the
names of any tools that are missing or have a non-bool value.
"""

from __future__ import annotations

import pytest

from roam.mcp_server import _TOOL_METADATA

REQUIRED_FLAGS = ("read_only", "destructive", "idempotent")


def _audit() -> dict[str, list[str]]:
    """Return {tool_name: [missing-or-wrong flags]}."""
    bad: dict[str, list[str]] = {}
    for name, meta in _TOOL_METADATA.items():
        problems = [flag for flag in REQUIRED_FLAGS if not isinstance(meta.get(flag), bool)]
        if problems:
            bad[name] = problems
    return bad


def test_all_tools_have_side_effect_metadata() -> None:
    assert _TOOL_METADATA, "_TOOL_METADATA is empty — did mcp_server import fail?"
    bad = _audit()
    if bad:
        lines = [f"  {name}: missing/non-bool flags {flags}" for name, flags in sorted(bad.items())]
        pytest.fail(f"{len(bad)} tool(s) lack side-effect metadata:\n" + "\n".join(lines))


def test_verify_declares_its_index_refresh_and_ledger_writes() -> None:
    meta = _TOOL_METADATA["roam_verify"]
    assert meta["read_only"] is False
    assert meta["destructive"] is False
    assert meta["idempotent"] is False
    assert meta["version"] == "1.1.0"


@pytest.mark.parametrize(
    ("tool_name", "destructive", "idempotent"),
    [
        ("roam_boundary", False, True),
        ("roam_cga_emit", False, False),
        ("roam_dogfood", False, False),
        ("roam_evidence_oscal", False, True),
        ("roam_fan", False, True),
        ("roam_fingerprint", False, True),
        ("roam_graph_diff", False, True),
        ("roam_metrics_push", False, False),
        ("roam_pr_analyze", False, False),
        ("roam_stale_refs", True, False),
        ("roam_test_hermeticity", False, True),
        ("roam_test_scaffold", False, False),
        ("roam_compile", False, False),
        ("roam_trends", False, False),
        ("roam_vuln_reach", False, True),
    ],
)
def test_option_dependent_write_tools_declare_maximum_effects(
    tool_name: str,
    destructive: bool,
    idempotent: bool,
) -> None:
    """A dry-run default never understates a wrapper's callable write path."""
    meta = _TOOL_METADATA[tool_name]
    assert meta["read_only"] is False
    assert meta["destructive"] is destructive
    assert meta["idempotent"] is idempotent
    assert meta["version"] == "1.1.0"


def test_read_only_wrappers_over_max_effect_commands_are_closed_and_audited() -> None:
    """Only wrappers that omit every CLI write control may narrow max effects."""
    import importlib

    from roam.capability import REGISTRY
    from roam.cli import _command_target
    from roam.mcp_server import (
        _MCP_READ_ONLY_CAPABILITY_PROJECTIONS,
        _mcp_tool_to_cli_command,
    )

    actual: set[str] = set()
    for tool_name, meta in _TOOL_METADATA.items():
        cli_name = _mcp_tool_to_cli_command(tool_name)
        target = _command_target(cli_name)
        if target is None:
            continue
        importlib.import_module(target[0])
        capability = REGISTRY.get(cli_name)
        if capability is not None and capability.side_effect and meta["read_only"]:
            actual.add(tool_name)

    assert actual == _MCP_READ_ONLY_CAPABILITY_PROJECTIONS, (
        "MCP read-only projection audit drifted: "
        f"unreviewed={sorted(actual - _MCP_READ_ONLY_CAPABILITY_PROJECTIONS)}, "
        f"stale={sorted(_MCP_READ_ONLY_CAPABILITY_PROJECTIONS - actual)}"
    )


def test_option_dependent_mcp_effect_rules_are_closed_and_match_cli_policy() -> None:
    """Concrete MCP narrowing must mirror an audited CLI write trigger."""
    import inspect

    import roam.mcp_server as mcp
    from roam.cli import _MODE_INVOCATION_ESCALATIONS

    assert mcp._MCP_OPTION_DEPENDENT_WRITE_FLAGS == {
        "roam_fan": frozenset({"persist"}),
    }
    for tool_name, write_flags in mcp._MCP_OPTION_DEPENDENT_WRITE_FLAGS.items():
        cli_name = mcp._mcp_tool_to_cli_command(tool_name)
        signature = inspect.signature(getattr(mcp, tool_name))
        for flag in write_flags:
            assert flag in signature.parameters
            cli_flag = f"--{flag.replace('_', '-')}"
            assert _MODE_INVOCATION_ESCALATIONS[cli_name][cli_flag] == "safe_edit"

        read_meta = mcp._effective_mcp_tool_metadata(tool_name, {})
        write_meta = mcp._effective_mcp_tool_metadata(
            tool_name,
            {next(iter(write_flags)): True},
        )
        assert read_meta["read_only"] is True
        assert read_meta["destructive"] is False
        assert read_meta["idempotent"] is True
        assert write_meta == mcp._TOOL_METADATA[tool_name]
