"""The serialized capability catalog describes invocable CLI commands."""

from __future__ import annotations

import json

from click.testing import CliRunner

from roam.cli import cli


def _catalog():
    result = CliRunner().invoke(cli, ["--budget", "0", "--json", "capabilities"])
    assert result.exit_code == 0, result.output
    return {cap["name"]: cap for cap in json.loads(result.output)["capabilities"]}


def test_compile_cache_is_one_group_with_invocable_subcommands():
    catalog = _catalog()
    assert not any(name.startswith("compile-cache-") for name in catalog)
    assert set(catalog["compile-cache"]["subcommands"]) == {"build", "clear", "evict", "stats", "vanilla-stats"}
    runner = CliRunner()
    for subcommand in catalog["compile-cache"]["subcommands"]:
        result = runner.invoke(cli, ["compile-cache", subcommand, "--help"])
        assert result.exit_code == 0, result.output


def test_mcp_and_deprecated_aliases_are_serialized():
    from roam.cli import _DEPRECATED_COMMANDS

    catalog = _catalog()
    assert catalog["mcp"]["requires_index"] is False
    assert catalog["mcp"]["ai_safe"] is False
    for alias, record in _DEPRECATED_COMMANDS.items():
        cap = catalog[alias]
        assert cap["deprecated"] is True
        assert cap["maturity"] == "deprecated"
        assert cap["replacement"] == record["replacement"]
        assert cap["ai_safe"] == catalog[record["replacement"]]["ai_safe"]
        result = CliRunner().invoke(cli, [alias, "--help"])
        assert result.exit_code == 0, result.output
