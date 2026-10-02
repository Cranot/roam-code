"""Keep algorithm guidance on the canonical CLI name and real MCP presets."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.commands.cmd_math import math_cmd
from roam.surface_counts import mcp_preset_members
from tests._helpers.repo_root import repo_root

ROOT = repo_root()


def test_algo_capability_presets_match_registered_membership():
    # Use the canonical AST-derived registry, not a second handwritten tool list.
    from roam.capability import REGISTRY

    cap = REGISTRY.get("algo")
    assert cap is not None
    actual = {name for name, tools in mcp_preset_members().items() if "roam_algo" in tools}
    assert actual, "An empty inventory cannot establish correct exposure"
    assert "core" not in actual  # This correction must not widen the default preset.
    assert set(cap.mcp_preset) == actual
    catalog = json.loads(json.dumps(REGISTRY.as_registry_catalog()))
    emitted = next(item for item in catalog["capabilities"] if item["name"] == "algo")
    assert set(emitted["mcp_preset"]) == actual


def test_algo_help_uses_canonical_followup_commands():
    result = CliRunner().invoke(cli, ["algo", "--help"], terminal_width=160)
    assert result.exit_code == 0, result.output
    help_text = " ".join(result.output.split())
    assert "Primary name: algo. Alias: math" in help_text
    assert "roam algo --list-frameworks" in help_text
    assert "roam algo --list-detectors" in help_text
    assert "roam --json algo" in help_text
    assert "roam math" not in help_text


def test_llms_algorithm_guidance_names_primary_before_legacy_alias():
    text = (ROOT / "templates/distribution/landing-page/llms.txt").read_text(encoding="utf-8")
    assert "roam algo (legacy alias: roam math)" in text
    assert "roam math (alias roam algo)" not in text
    assert "-> math/algo" not in text
    assert "`ROAM_MCP_PRESET=review`" in text
    assert "roam_algo" in mcp_preset_members()["review"]


@pytest.mark.parametrize("name", ["algo", "math"])
def test_canonical_and_legacy_alias_retain_index_free_task_listing(name, monkeypatch, tmp_path):
    # Temporary cwd prevents depending on this checkout's index; forbid auto-index.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ROAM_NO_AUTO_INDEX", "1")
    result = CliRunner().invoke(cli, ["--json", "--budget", "0", name, "--list-tasks"])
    assert result.exit_code == 0, result.output
    # Click's combined output includes the alias deprecation on stderr.
    data = json.loads(result.stdout)
    assert data["command"] == "algo"
    assert data["summary"]["partial_success"] is False
    assert data["summary"]["task_count"] == len(data["tasks"]) > 0
    assert not (tmp_path / ".roam").exists()


def test_alias_remains_the_same_registered_command():
    from roam.cli import _COMMANDS, _DEPRECATED_COMMANDS

    assert _COMMANDS["algo"] == _COMMANDS["math"]
    assert "math" in _DEPRECATED_COMMANDS
    assert cli.get_command(None, "algo") is math_cmd
