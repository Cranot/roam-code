"""Tests for the shipped community rule pack."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from roam.cli import cli
from roam.rules.engine import load_rules


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_load_rules_recurses_subdirectories(tmp_path):
    rules_dir = tmp_path / "rules"
    (rules_dir / "security").mkdir(parents=True)
    (rules_dir / "style").mkdir(parents=True)

    (rules_dir / "security" / "a.yaml").write_text(
        'name: "A"\n'
        "severity: warning\n"
        "type: ast_match\n"
        "match:\n"
        '  ast: "eval($X)"\n'
        "  language: python\n"
        '  file_glob: "**/*.py"\n',
        encoding="utf-8",
    )
    (rules_dir / "style" / "b.yml").write_text(
        'name: "B"\n'
        "severity: info\n"
        "match:\n"
        "  kind: [function]\n",
        encoding="utf-8",
    )

    rules = load_rules(rules_dir)
    assert len(rules) == 2
    names = sorted(r["name"] for r in rules)
    assert names == ["A", "B"]


def test_community_pack_has_500_plus_valid_rules():
    rules_dir = _repo_root() / "rules" / "community"
    rules = load_rules(rules_dir)

    assert len(rules) >= 500
    parse_errors = [r for r in rules if "_error" in r]
    assert parse_errors == []


def test_rules_command_can_run_community_pack(indexed_project, monkeypatch):
    rules_dir = _repo_root() / "rules" / "community"

    monkeypatch.chdir(indexed_project)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["rules", "--rules-dir", str(rules_dir)],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert "VERDICT:" in result.output
