"""Tests for the deterministic ``roam cycle-break`` recommender."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from conftest import index_in_process, invoke_cli, parse_json_output  # noqa: E402


def _indexed_project(tmp_path, monkeypatch, files: dict[str, str]) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()
    for name, source in files.items():
        (project / name).write_text(source)
    monkeypatch.chdir(project)
    output, exit_code = index_in_process(project, "--force")
    assert exit_code == 0, output
    return project


def test_cycle_break_names_closing_edge_symbol_and_extraction(cli_runner, tmp_path, monkeypatch):
    project = _indexed_project(
        tmp_path,
        monkeypatch,
        {
            "a.py": "from b import b_symbol\n\n\ndef a_symbol():\n    return 'a'\n\n\ndef use_b():\n    return b_symbol()\n",
            "b.py": "from c import c_symbol\n\n\ndef b_symbol():\n    return 'b'\n\n\ndef use_c():\n    return c_symbol()\n",
            "c.py": "from a import a_symbol\n\n\ndef c_symbol():\n    return 'c'\n\n\ndef use_a():\n    return a_symbol()\n",
        },
    )

    result = invoke_cli(cli_runner, ["cycle-break", "--json"], cwd=project)
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="cycle-break")

    assert data["summary"]["cycle_count"] == 1
    cycle = data["cycles"][0]
    assert [member["file"] for member in cycle["members"]] == ["a.py", "b.py", "c.py"]
    assert cycle["recommendation_state"] == "resolved"
    assert len(cycle["closing_edges"]) == 1
    closing = cycle["closing_edges"][0]
    assert closing["source"]["file"] == "c.py"
    assert closing["target"]["file"] == "a.py"
    assert [symbol["name"] for symbol in closing["symbols"]] == ["a_symbol"]
    assert "Extract symbol `a_symbol` from `a.py`" in cycle["recommendations"][0]
    assert "a.py → b.py → c.py → a.py" in cycle["recommendations"][0]

    text_result = invoke_cli(cli_runner, ["cycle-break"], cwd=project)
    assert text_result.exit_code == 0, text_result.output
    assert "closing edge: c.py → a.py" in text_result.output
    assert "symbols: a_symbol" in text_result.output


def test_cycle_break_acyclic_project_has_no_findings(cli_runner, tmp_path, monkeypatch):
    project = _indexed_project(
        tmp_path,
        monkeypatch,
        {
            "a.py": "def a_symbol():\n    return 'a'\n",
            "b.py": "from a import a_symbol\n\n\ndef b_symbol():\n    return a_symbol()\n",
        },
    )

    result = invoke_cli(cli_runner, ["cycle-break", "--json"], cwd=project)
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="cycle-break")
    assert data["summary"]["cycle_count"] == 0
    assert data["cycles"] == []

    text_result = invoke_cli(cli_runner, ["cycle-break"], cwd=project)
    assert text_result.exit_code == 0
    assert text_result.output == ""
