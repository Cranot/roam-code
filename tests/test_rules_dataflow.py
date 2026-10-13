"""Tests for dataflow_match custom rule support (#142)."""

from __future__ import annotations

import json
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
        "src/app.py": ("def compute(a):\n    temp = a\n    unused = 1\n    return temp\n"),
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
        "src/app.py": ("def greet(name, punctuation):\n    return f'hello {name}'\n"),
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
        "src/app.py": ("def run():\n    user = input('value: ')\n    eval(user)\n    return 1\n"),
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
        "src/app.py": ("def run(x):\n    tmp = x\n    dead = 42\n    return tmp\n"),
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


def _run_rules(proj: Path):
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        with open_db(readonly=True) as conn:
            return evaluate_all(proj / ".roam" / "rules", conn)
    finally:
        os.chdir(old_cwd)


def _sink_rule(sanitizers: str | None) -> dict[str, str]:
    body = (
        'name: "Command injection"\n'
        "severity: error\n"
        "type: dataflow_match\n"
        "match:\n"
        "  patterns: [source_to_sink]\n"
        '  file_glob: "**/*.py"\n'
        '  sources: ["input("]\n'
        '  sinks: ["os.system("]\n'
    )
    if sanitizers is not None:
        body += f"  sanitizers: {sanitizers}\n"
    return {"cmd.yaml": body}


_UNSANITIZED = "import os\n\n\ndef run():\n    v = input()\n    os.system(v)\n"
_SANITIZED = "import os\nimport shlex\n\n\ndef run():\n    v = input()\n    os.system(shlex.quote(v))\n"
_SANITIZED_EARLIER = (
    "import os\nimport shlex\n\n\ndef run():\n    v = input()\n    v = shlex.quote(v)\n    os.system(v)\n"
)


def test_sanitizers_suppress_sanitized_flow(tmp_path):
    proj = _make_project(tmp_path, {"src/app.py": _SANITIZED}, _sink_rule('["shlex.quote("]'))
    results = _run_rules(proj)
    assert results[0]["passed"] is True, results[0]["violations"]


def test_sanitizers_suppress_sanitizer_between_source_and_sink(tmp_path):
    proj = _make_project(tmp_path, {"src/app.py": _SANITIZED_EARLIER}, _sink_rule('["shlex.quote("]'))
    results = _run_rules(proj)
    assert results[0]["passed"] is True, results[0]["violations"]


def test_sanitizers_control_unsanitized_flow_still_flagged(tmp_path):
    proj = _make_project(tmp_path, {"src/app.py": _UNSANITIZED}, _sink_rule('["shlex.quote("]'))
    results = _run_rules(proj)
    assert results[0]["passed"] is False
    assert any(v.get("type") == "source_to_sink" for v in results[0]["violations"])


def test_sanitizers_control_rule_without_key_unchanged(tmp_path):
    proj = _make_project(tmp_path, {"src/app.py": _SANITIZED}, _sink_rule(None))
    results = _run_rules(proj)
    assert results[0]["passed"] is False


def test_sanitizers_unrelated_string_does_not_suppress(tmp_path):
    proj = _make_project(tmp_path, {"src/app.py": _UNSANITIZED}, _sink_rule('["escape("]'))
    results = _run_rules(proj)
    assert results[0]["passed"] is False


_QUOTED_THEN_RAW = (
    "import os\nimport shlex\n\n\ndef run():\n    v = input()\n    os.system(shlex.quote(v))\n    os.system(v)\n"
)
_QUOTED_ONLY_TWICE = (
    "import os\nimport shlex\n\n\ndef run():\n"
    "    v = input()\n    os.system(shlex.quote(v))\n    os.system(shlex.quote(v))\n"
)
_SANITIZED_THEN_RAW_SINK_AFTER_REASSIGN = (
    "import os\nimport shlex\n\n\ndef run():\n"
    "    v = input()\n    v = shlex.quote(v)\n    os.system(v)\n    os.system(v)\n"
)


def _check_rules_cli(proj: Path, monkeypatch, rule_name: str = "Command injection"):
    monkeypatch.chdir(proj)
    result = invoke_cli(CliRunner(), ["check-rules", "--rule", rule_name], cwd=proj, json_mode=True)
    # A failing rule exits 1, so parse the JSON directly instead of parse_json_output.
    data = json.loads(result.output)
    rows = [r for r in data.get("results", []) if r.get("id") == rule_name]
    assert rows, data.get("results", [])
    return result, rows[0]


def test_sanitized_sink_then_raw_sink_cli(tmp_path, monkeypatch):
    # Quoting the first sink's argument does not sanitize v for the second sink.
    proj = _make_project(tmp_path, {"src/app.py": _QUOTED_THEN_RAW}, _sink_rule('["shlex.quote("]'))
    result, row = _check_rules_cli(proj, monkeypatch)
    assert row["passed"] is False
    assert result.exit_code == 1


def test_sanitized_sink_twice_cli_still_clean(tmp_path, monkeypatch):
    proj = _make_project(tmp_path, {"src/app.py": _QUOTED_ONLY_TWICE}, _sink_rule('["shlex.quote("]'))
    result, row = _check_rules_cli(proj, monkeypatch)
    assert row["passed"] is True
    assert result.exit_code == 0


def test_sanitized_value_replacement_cli_clean(tmp_path, monkeypatch):
    # A sanitizer on its own line replaces the value: later sinks are clean.
    proj = _make_project(
        tmp_path, {"src/app.py": _SANITIZED_THEN_RAW_SINK_AFTER_REASSIGN}, _sink_rule('["shlex.quote("]')
    )
    result, row = _check_rules_cli(proj, monkeypatch)
    assert row["passed"] is True
    assert result.exit_code == 0
