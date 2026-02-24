"""Tests for AST-based custom rules with `$METAVAR` matching."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from roam.cli import cli
from roam.db.connection import open_db
from roam.rules.engine import evaluate_all


def _write_rules(project_path: Path, rule_files: dict[str, str]) -> Path:
    rules_dir = project_path / ".roam" / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    for name, content in rule_files.items():
        (rules_dir / name).write_text(content, encoding="utf-8")
    return rules_dir


def test_ast_match_rule_finds_eval_calls(project_factory, monkeypatch):
    proj = project_factory({
        "src/app.py": (
            "def run_expr(code):\n"
            "    eval(code)\n"
            "    answer = eval('1 + 1')\n"
            "    return answer\n"
        ),
    })
    rules_dir = _write_rules(proj, {
        "no_eval.yaml": (
            'name: "No eval calls"\n'
            "severity: error\n"
            "type: ast_match\n"
            "match:\n"
            '  ast: "eval($EXPR)"\n'
            "  language: python\n"
            '  file_glob: "**/*.py"\n'
        ),
    })

    monkeypatch.chdir(proj)
    with open_db(readonly=True) as conn:
        results = evaluate_all(rules_dir, conn)

    assert len(results) == 1
    result = results[0]
    assert result["passed"] is False
    assert len(result["violations"]) == 2
    reasons = [v.get("reason", "") for v in result["violations"]]
    assert any("$EXPR=" in reason for reason in reasons)


def test_ast_match_repeated_metavar_requires_same_subtree(project_factory, monkeypatch):
    proj = project_factory({
        "src/app.py": (
            "def compare(a, b):\n"
            "    same(a, a)\n"
            "    same(a, b)\n"
        ),
    })
    rules_dir = _write_rules(proj, {
        "same_args.yaml": (
            'name: "same args only"\n'
            "severity: warning\n"
            "type: ast_match\n"
            "match:\n"
            '  ast: "same($X, $X)"\n'
            "  language: python\n"
            '  file_glob: "**/*.py"\n'
        ),
    })

    monkeypatch.chdir(proj)
    with open_db(readonly=True) as conn:
        results = evaluate_all(rules_dir, conn)

    assert len(results) == 1
    result = results[0]
    assert result["passed"] is False
    assert len(result["violations"]) == 1


def test_ast_match_rule_passes_when_no_match(project_factory, monkeypatch):
    proj = project_factory({
        "src/app.py": (
            "def run_expr(code):\n"
            "    return code + 'safe'\n"
        ),
    })
    rules_dir = _write_rules(proj, {
        "no_exec.yaml": (
            'name: "No exec calls"\n'
            "severity: error\n"
            "type: ast_match\n"
            "match:\n"
            '  ast: "exec($EXPR)"\n'
            "  language: python\n"
            '  file_glob: "**/*.py"\n'
        ),
    })

    monkeypatch.chdir(proj)
    with open_db(readonly=True) as conn:
        results = evaluate_all(rules_dir, conn)

    assert len(results) == 1
    assert results[0]["passed"] is True
    assert results[0]["violations"] == []


def test_check_rules_includes_custom_ast_rules(project_factory, monkeypatch):
    proj = project_factory({
        "src/app.py": (
            "def run_expr(code):\n"
            "    eval(code)\n"
        ),
    })
    _write_rules(proj, {
        "no_eval.yaml": (
            'name: "No eval calls"\n'
            "severity: error\n"
            "type: ast_match\n"
            "match:\n"
            '  ast: "eval($EXPR)"\n'
            "  language: python\n"
            '  file_glob: "**/*.py"\n'
        ),
    })

    monkeypatch.chdir(proj)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["check-rules", "--rule", "No eval calls"],
        catch_exceptions=False,
    )

    assert result.exit_code == 1
    assert "No eval calls" in result.output
