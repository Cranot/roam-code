"""Compact grouped inspection for ``roam verify --summary``."""

from __future__ import annotations

import sys
from pathlib import Path

from click.testing import CliRunner

from roam.commands.cmd_verify import _emit_verify_summary, verify

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process, invoke_cli, parse_json_output


def _finding(file: str, severity: str, category: str, symbol: str, line: int, *, blast: int = 0) -> dict:
    return {
        "file": file,
        "severity": severity,
        "category": category,
        "symbol": symbol,
        "line": line,
        "blast_radius": blast,
        "message": f"message for {symbol}",
    }


def test_verify_summary_groups_findings_and_selects_one_top_item(capsys):
    findings = [
        _finding("src/a.py", "WARN", "naming", "small_name", 4, blast=1),
        _finding("src/a.py", "WARN", "naming", "top_name", 20, blast=9),
        _finding("src/a.py", "WARN", "naming", "other_name", 30, blast=2),
        _finding("src/a.py", "FAIL", "imports", "bad_import", 2),
        _finding("src/b.py", "INFO", "syntax", "style_note", 8),
        _finding("src/b.py", "INFO", "syntax", "another_note", 12),
    ]

    _emit_verify_summary(findings, files_checked=2)
    output = capsys.readouterr().out

    assert "VERIFY SUMMARY: 6 findings across 2 files" in output
    assert "FILE: src/a.py (4 findings)" in output
    assert "FAIL / imports: 1 finding" in output
    assert "WARN / naming: 3 findings" in output
    assert "TOP: top_name @ src/a.py:20 -- message for top_name" in output
    assert "TOP: bad_import @ src/a.py:2 -- message for bad_import" in output
    assert "FILE: src/b.py (2 findings)" in output
    assert "INFO / syntax: 2 findings" in output
    assert "TOP: style_note @ src/b.py:8 -- message for style_note" in output
    assert "TOP: small_name" not in output
    assert "TOP: other_name" not in output


def test_verify_summary_empty_set_is_clean(capsys):
    _emit_verify_summary([], files_checked=0)

    assert capsys.readouterr().out == "VERIFY SUMMARY: 0 findings across 0 files\n  OK -- all checks passed\n"


def test_verify_summary_does_not_replace_json_detail(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (repo / "README.md").write_text("Roam is the fastest local code intelligence tool.\n", encoding="utf-8")
    git_init(repo)
    monkeypatch.chdir(repo)
    out, rc = index_in_process(repo, "--force")
    assert rc == 0, out

    text_result = invoke_cli(CliRunner(), ["verify", "--summary", "--checks", "claims", "README.md"], cwd=repo)
    assert text_result.exit_code == 0
    assert "VERIFY SUMMARY: 1 finding across 1 file" in text_result.output

    default_result = invoke_cli(CliRunner(), ["verify", "--checks", "claims", "README.md"], cwd=repo)
    assert default_result.exit_code == 0
    assert default_result.output.startswith("VERDICT:")
    assert "VERIFY SUMMARY" not in default_result.output

    result = invoke_cli(
        CliRunner(),
        ["verify", "--summary", "--checks", "claims", "README.md"],
        cwd=repo,
        json_mode=True,
    )
    envelope = parse_json_output(result)

    assert result.exit_code == 0
    assert "VERIFY SUMMARY" not in result.output
    assert envelope["categories"]["claims"]["violations"]
    assert envelope["violations"]


def test_verify_summary_is_exposed_as_a_cli_option():
    result = CliRunner().invoke(verify, ["--help"])

    assert result.exit_code == 0
    assert "--summary" in result.output
