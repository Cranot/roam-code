"""Canonical surface names follow the CLI alias authority, not callback names."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from roam import surface_counts
from roam.cli import cli
from roam.commands import cmd_surface_gaps


def _source(tmp_path, monkeypatch, aliases):
    path = tmp_path / "cli.py"
    # Deliberately raises if imported: registry discovery must stay AST-only.
    path.write_text(
        "raise RuntimeError('source must not execute')\n"
        "_COMMANDS = {'algo': ('m', 'math_cmd'), 'math': ('m', 'math_cmd'), "
        "'other': ('n', 'other_cmd')}\n" + aliases,
        encoding="utf-8",
    )
    monkeypatch.setattr(surface_counts, "_package_file", lambda *args: path)


@pytest.mark.parametrize("record", ["{'replacement': 'algo'}", "'algo'"])
def test_declared_alias_overrides_old_callback(tmp_path, monkeypatch, record):
    _source(tmp_path, monkeypatch, "_DEPRECATED_COMMANDS = {'math': " + record + "}\n")
    assert surface_counts.canonical_cli_commands() == ["algo", "other"]


@pytest.mark.parametrize(
    "aliases",
    [
        "",
        "_DEPRECATED_COMMANDS = []",
        "_DEPRECATED_COMMANDS = {'math': None}",
        "_DEPRECATED_COMMANDS = {'math': 'missing'}",
        "_DEPRECATED_COMMANDS = {'math': 'other'}",
        "_DEPRECATED_COMMANDS = {'math': 'algo', 'algo': 'math'}",
    ],
)
def test_invalid_alias_authority_is_unknown_in_serialized_consumer(tmp_path, monkeypatch, aliases):
    _source(tmp_path, monkeypatch, aliases)
    monkeypatch.setattr(cmd_surface_gaps, "_resolve_documented_commands", lambda: ({"algo"}, None))
    result = CliRunner().invoke(cli, ["--json", "--budget", "0", "surface-gaps"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["layers"]["implementation"] is False
    assert data["summary"]["partial_success"] is True
    assert any("implementation layer unavailable" in warning for warning in data["warnings"])
    assert not any(f["gap"] == "documented_not_implemented" for f in data["findings"])


def test_real_registry_preserves_canonical_names_and_count():
    names = surface_counts.canonical_cli_commands()
    assert len(names) == 280
    assert {"algo", "understand", "weather", "trends", "uses"} <= set(names)
    assert not {"math", "onboard", "churn", "digest", "refs", "snapshot", "trend"} & set(names)
