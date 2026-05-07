"""Tests for roam skill-generate.

Generates an agent-runtime skill manifest from the capability registry.
Targets: claude (SKILL.md), cursor (.mdc rule), continue (config snippet),
aider (.aiderrc).
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from roam.commands.cmd_skill_generate import skill_generate_cmd


def test_target_claude_emits_skill_md_with_frontmatter() -> None:
    runner = CliRunner()
    result = runner.invoke(skill_generate_cmd, ["--target", "claude"], obj={})
    assert result.exit_code == 0, result.output
    assert "---" in result.output
    assert "name: roam" in result.output
    assert "description:" in result.output
    assert "# Roam — Codebase Comprehension Skill" in result.output
    assert "Repository:" in result.output


def test_target_cursor_emits_mdc_rule() -> None:
    runner = CliRunner()
    result = runner.invoke(skill_generate_cmd, ["--target", "cursor"], obj={})
    assert result.exit_code == 0
    assert "globs: ['**/*']" in result.output
    assert "alwaysApply: false" in result.output


def test_target_continue_emits_valid_json() -> None:
    runner = CliRunner()
    result = runner.invoke(skill_generate_cmd, ["--target", "continue"], obj={})
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "slashCommands" in data
    assert isinstance(data["slashCommands"], list)


def test_target_aider_emits_aiderrc_snippet() -> None:
    runner = CliRunner()
    result = runner.invoke(skill_generate_cmd, ["--target", "aider"], obj={})
    assert result.exit_code == 0
    assert "[aliases]" in result.output


def test_ai_safe_only_default_filters_capabilities() -> None:
    runner = CliRunner()
    result = runner.invoke(skill_generate_cmd, ["--target", "claude"], obj={})
    assert result.exit_code == 0
    # The Phase 0 commands should appear in the body
    body = result.output
    assert "permit" in body or "postmortem" in body or "article-12-check" in body


def test_output_path_writes_to_file(tmp_path) -> None:
    runner = CliRunner()
    out = tmp_path / "SKILL.md"
    result = runner.invoke(skill_generate_cmd, ["--target", "claude", "--output", str(out)], obj={})
    assert result.exit_code == 0
    assert out.exists()
    contents = out.read_text(encoding="utf-8")
    assert "name: roam" in contents


def test_unknown_target_errors() -> None:
    runner = CliRunner()
    result = runner.invoke(skill_generate_cmd, ["--target", "nonsense"], obj={})
    # click rejects bad choice with exit_code != 0
    assert result.exit_code != 0
