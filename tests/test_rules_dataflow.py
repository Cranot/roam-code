"""Tests for dataflow_match custom rule support (#142)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process, invoke_cli, parse_json_output

from roam.db.connection import open_db
from roam.rules.engine import evaluate_all


def _make_project(tmp_path: Path, files: dict[str, str], rules: dict[str, str]) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")

    for rel, content in files.items():
        p = proj / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    rules_dir = proj / ".roam" / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    for name, content in rules.items():
        (rules_dir / name).write_text(content, encoding="utf-8")

    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    return proj


def test_dataflow_rule_detects_dead_assignment(tmp_path):
    files = {
        "src/app.py": (
            "def compute(a):\n"
            "    temp = a\n"
            "    unused = 1\n"
            "    return temp\n"
        ),
    }
    rules = {
        "dead_assign.yaml": (
            'name: "Dead assignment detector"\n'
            "severity: warning\n"
            "type: dataflow_match\n"
            "match:\n"
            "  patterns: [dead_assignment]\n"
            '  file_glob: "**/*.py"\n'
        ),
    }
    proj = _make_project(tmp_path, files, rules)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        with open_db(readonly=True) as conn:
            results = evaluate_all(proj / ".roam" / "rules", conn)
    finally:
        os.chdir(old_cwd)

    assert len(results) == 1
    assert results[0]["passed"] is False
    reasons = [v.get("reason", "") for v in results[0]["violations"]]
    assert any("assigned but never read" in reason for reason in reasons)


def test_dataflow_rule_detects_unused_param(tmp_path):
    files = {
        "src/app.py": (
            "def greet(name, punctuation):\n"
            "    return f'hello {name}'\n"
        ),
    }
    rules = {
        "unused_param.yaml": (
            'name: "Unused param detector"\n'
            "severity: warning\n"
            "type: dataflow_match\n"
            "match:\n"
            "  patterns: [unused_param]\n"
            '  file_glob: "**/*.py"\n'
        ),
    }
    proj = _make_project(tmp_path, files, rules)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        with open_db(readonly=True) as conn:
            results = evaluate_all(proj / ".roam" / "rules", conn)
    finally:
        os.chdir(old_cwd)

    assert len(results) == 1
    assert results[0]["passed"] is False
    reasons = [v.get("reason", "") for v in results[0]["violations"]]
    assert any("parameter 'punctuation' is never read" in reason for reason in reasons)


def test_dataflow_rule_detects_source_to_sink(tmp_path):
    files = {
        "src/app.py": (
            "def run():\n"
            "    user = input('value: ')\n"
            "    eval(user)\n"
            "    return 1\n"
        ),
    }
    rules = {
        "src_sink.yaml": (
            'name: "Source to sink detector"\n'
            "severity: error\n"
            "type: dataflow_match\n"
            "match:\n"
            "  patterns: [source_to_sink]\n"
            '  file_glob: "**/*.py"\n'
            "  max_matches: 10\n"
        ),
    }
    proj = _make_project(tmp_path, files, rules)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        with open_db(readonly=True) as conn:
            results = evaluate_all(proj / ".roam" / "rules", conn)
    finally:
        os.chdir(old_cwd)

    assert len(results) == 1
    assert results[0]["passed"] is False
    assert any(v.get("type") == "source_to_sink" for v in results[0]["violations"])


def test_check_rules_includes_custom_dataflow_rule(tmp_path, monkeypatch):
    files = {
        "src/app.py": (
            "def run(x):\n"
            "    tmp = x\n"
            "    dead = 42\n"
            "    return tmp\n"
        ),
    }
    rules = {
        "custom_dataflow.yaml": (
            'name: "Custom dead assignment rule"\n'
            "severity: warning\n"
            "type: dataflow_match\n"
            "match:\n"
            "  patterns: [dead_assignment]\n"
            '  file_glob: "**/*.py"\n'
        ),
    }
    proj = _make_project(tmp_path, files, rules)
    monkeypatch.chdir(proj)

    runner = CliRunner()
    result = invoke_cli(
        runner,
        ["check-rules", "--rule", "Custom dead assignment rule"],
        cwd=proj,
        json_mode=True,
    )
    data = parse_json_output(result, "check-rules")
    custom = [r for r in data.get("results", []) if r.get("id") == "Custom dead assignment rule"]
    assert custom, f"custom rule missing from output: {data.get('results', [])}"
    assert custom[0]["passed"] is False
