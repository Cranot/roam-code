"""Focused tests for the three-layer ``roam surface-gaps`` audit."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from roam.cli import cli
from roam.commands import cmd_surface_gaps
from tests.conftest import git_init


def _invoke_with_layers(monkeypatch, *, implementation, mcp_exposed, documented):
    monkeypatch.setattr(
        cmd_surface_gaps,
        "_resolve_implementation_commands",
        lambda: (set(implementation), None),
    )
    monkeypatch.setattr(
        cmd_surface_gaps,
        "_resolve_mcp_commands",
        lambda _implementation: (set(mcp_exposed), None),
    )
    monkeypatch.setattr(
        cmd_surface_gaps,
        "_resolve_documented_commands",
        lambda: (set(documented), None),
    )

    result = CliRunner().invoke(cli, ["--json", "surface-gaps"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_surface_gaps_reports_each_resolvable_gap(monkeypatch):
    data = _invoke_with_layers(
        monkeypatch,
        implementation={"alpha", "beta", "gamma"},
        mcp_exposed={"alpha", "gamma"},
        documented={"alpha", "beta", "ghost"},
    )

    assert data["findings"] == [
        {
            "command": "beta",
            "gap": "implemented_not_mcp_exposed",
            "message": "implemented, not MCP-exposed",
        },
        {
            "command": "gamma",
            "gap": "undocumented_command",
            "message": "undocumented command",
        },
        {
            "command": "ghost",
            "gap": "documented_not_implemented",
            "message": "documented but not implemented",
        },
    ]
    assert data["summary"]["gap_count"] == 3
    assert data["summary"]["partial_success"] is False


def test_surface_gaps_reports_zero_for_consistent_layers(monkeypatch):
    data = _invoke_with_layers(
        monkeypatch,
        implementation={"alpha", "beta"},
        mcp_exposed={"alpha", "beta"},
        documented={"alpha", "beta"},
    )

    assert data["findings"] == []
    assert data["summary"]["verdict"] == "No surface gaps"
    assert data["summary"]["gap_count"] == 0


def test_documentation_parser_excludes_hidden_and_alias_rows():
    documented = cmd_surface_gaps._parse_documented_commands(
        """\
| Command | Maturity | MCP | Aliases |
|---------|----------|-----|---------|
| `public-command` | stable | ✓ | — |
| `hidden-command` | internal | — | — |
| `old-alias` | deprecated | ✓ | public-command |
"""
    )

    assert documented == {"public-command"}


def test_surface_gaps_skips_comparison_when_a_layer_is_unresolved():
    findings = cmd_surface_gaps._find_surface_gaps(
        implementation={"alpha"},
        mcp_exposed=None,
        documented={"alpha"},
    )

    assert findings == []


def test_documentation_layer_uses_analyzed_repository(tmp_path, monkeypatch):
    project = tmp_path / "analyzed-repository"
    docs = project / "docs"
    docs.mkdir(parents=True)
    (docs / "COMMANDS.md").write_text(
        "| Command | Maturity | MCP | Aliases |\n"
        "|---------|----------|-----|---------|\n"
        "| `surface-gaps` | stable | — | — |\n"
    )
    git_init(project)
    monkeypatch.chdir(project)

    # Reproduce an installed-package/out-of-tree invocation: the analyzed
    # repository is not an ancestor of the module that supplies the detector.
    import roam.surface_counts as surface_counts

    monkeypatch.setattr(surface_counts, "__file__", str(Path("/opt/site-packages/roam/surface_counts.py")))

    result = CliRunner().invoke(cli, ["--json", "surface-gaps"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    assert data["layers"]["documentation"] is True
    assert "implementation:documentation" in data["comparisons_run"]
    assert "documentation:implementation" in data["comparisons_run"]
    assert not any("documentation layer unavailable" in warning for warning in data["warnings"])
    assert not any(
        finding["command"] == "surface-gaps" and finding["gap"] == "undocumented_command"
        for finding in data["findings"]
    )
