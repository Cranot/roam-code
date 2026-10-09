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
