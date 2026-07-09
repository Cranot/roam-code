"""Tests for extraction-hint enrichment in ``suggest-refactoring``."""

from __future__ import annotations

from tests.conftest import (
    assert_json_envelope,
    invoke_cli,
    parse_json_output,
)
from tests.conftest import (
    make_src_project as _make_project,
)

_PROJECT_SOURCE = (
    "\n".join(
        [
            "def freshly_extracted_helper(x):",
            "    for i in x:",
            "        if i:",
            "            if i > 1:",
            "                if i > 2:",
            "                    return i",
            "    if x:",
            "        return 0",
            "    return 0",
            *["    # pad"] * 90,
            "",
            "def plain_helper(value):",
            "    return value + 1",
            "",
        ]
    )
    + "\n"
)


def _project(tmp_path):
    return _make_project(tmp_path, {"helpers.py": _PROJECT_SOURCE})


def test_suggest_refactoring_json_includes_extraction_hint(cli_runner, tmp_path, monkeypatch):
    proj = _project(tmp_path)
    monkeypatch.chdir(proj)

    assert invoke_cli(cli_runner, ["index"], cwd=proj).exit_code == 0

    result = invoke_cli(
        cli_runner,
        ["suggest-refactoring", "--min-score", "0", "--limit", "5"],
        cwd=proj,
        json_mode=True,
    )
    data = parse_json_output(result, "suggest-refactoring")
    assert_json_envelope(data, "suggest-refactoring")

    rec = next(r for r in data["recommendations"] if r["name"] == "freshly_extracted_helper")
    assert rec["action"] == "extract"
    hint = rec["extraction_hint"]
    assert hint == {
        "block": "if block",
        "line_start": 4,
        "line_end": 6,
        "line_count": 3,
        "reduction": 11.0,
        "parent_after": 4.0,
        "helper_cc": 3.0,
    }
    plain = next(r for r in data["recommendations"] if r["name"] == "plain_helper")
    assert "extraction_hint" not in plain


def test_suggest_refactoring_text_surfaces_extraction_hint(cli_runner, tmp_path, monkeypatch):
    proj = _project(tmp_path)
    monkeypatch.chdir(proj)

    assert invoke_cli(cli_runner, ["index"], cwd=proj).exit_code == 0

    result = invoke_cli(
        cli_runner,
        ["suggest-refactoring", "--min-score", "0", "--limit", "5"],
        cwd=proj,
    )
    assert result.exit_code == 0, result.output
    assert "freshly_extracted_helper" in result.output
    assert "extract if block" in result.output
    assert "CC 15->4 (-11)" in result.output
    assert "helper ~3" in result.output
