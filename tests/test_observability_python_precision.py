"""Executable Python calls, not examples, drive observability candidates."""

from __future__ import annotations

import json
import subprocess

import pytest
from click.testing import CliRunner

from roam import observability_opt as oo
from roam.cli import cli


@pytest.mark.parametrize(
    "source,lines",
    [
        ('"""Example:\nprint(value)\n"""\nprint("real")\n', [4]),
        ('example = "print(value)"\nprint("real")\n', [2]),
        ('value = 1  # print(value)\nprint("real")\n', [2]),
        ('print("a"); print("b")\n', [1]),
        ('print\n("example")\n', []),
        ('(print)("real")\n', [1]),
        ('print(\n "real"\n)\n', [1]),
        ('f"example print(value) {print(123)}"\n', [1]),
        ('obj.print("method")\n', []),
        ('import sys\nprint("diagnostic", file=sys.stderr)\n', [2]),
        ('text = "example\u2028line"\nprint("real")\n', [2]),
        ('\fprint("real")\n', [1]),
        ('text = "example"\rprint("real")\r', [2]),
    ],
)
def test_python_calls_not_text_mentions(source, lines):
    findings = oo.detect_print_debug_leftover([("app.py", "python", source)])
    assert [f["evidence"]["lineno"] for f in findings] == lines
    for finding in findings:
        assert finding["confidence_basis"] == "heuristic"
        assert finding["evidence"]["detection_method"] == "python_ast_call"
        assert "print" in finding["evidence"]["line"]
        assert "review" in finding["reason"].lower()


def test_unparseable_python_is_not_a_clean_negative():
    findings, meta = oo.run_observability_opt(
        None,
        sources=[("broken.py", "python", 'def broken(:\n    print("x")\n'), ("valid.py", "python", 'print("real")\n')],
    )
    assert [f["subject"] for f in findings] == ["valid.py:1"]
    assert meta["partial_success"] is True
    assert meta["sources"]["files_unparsed"] == ["broken.py"]


def test_unreadable_source_is_partial(monkeypatch):
    monkeypatch.setattr(
        oo,
        "harvest_source_files",
        lambda *a, **kw: (
            [("valid.py", "python", "value = 1\n")],
            ["missing.py"],
        ),
    )
    _, meta = oo.run_observability_opt(None)
    assert meta["partial_success"] is True
    assert meta["sources"]["files_unreadable"] == ["missing.py"]


def test_real_indexed_cli_preserves_calls_and_discloses_parse_failure(tmp_path, monkeypatch):
    from roam.index.indexer import Indexer

    (tmp_path / "app.py").write_text('"""Example:\nprint(x)\n"""\nprint("real")\n', encoding="utf-8")
    (tmp_path / "broken.py").write_text('def bad(:\n    print("unknown")\n', encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "app.py", "broken.py"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    Indexer().run(quiet=True)
    result = CliRunner().invoke(cli, ["--json", "observability-opt", "--top", "0"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert [f["subject"] for f in payload["findings"]] == ["app.py:4"]
    assert payload["summary"]["files_unparsed"] == ["broken.py"]
    assert payload["summary"]["partial_success"] is True
    assert "unparsed" in payload["summary"]["verdict"]
    text_result = CliRunner().invoke(cli, ["observability-opt"])
    assert "unparsed" in text_result.stdout
    from roam.mcp_server import observability_opt

    wrapped = observability_opt(language="python", root=str(tmp_path))
    assert wrapped["summary"]["files_unparsed"] == ["broken.py"]
    assert wrapped["summary"]["partial_success"] is True
    assert [f["subject"] for f in wrapped["findings"]] == ["app.py:4"]


def test_predicate_version_is_visible_in_registry_and_persistence():
    assert oo.list_observability_opt_detectors()[0]["version"] == "1.1.0"
    findings = oo.detect_print_debug_leftover([("app.py", "python", "print(1)\n")])
    assert oo.build_finding_records(findings)[0].source_version == "1.1.0"


def test_mcp_keeps_source_gaps_and_real_positive(project_factory):
    from roam.mcp_server import observability_opt

    root = project_factory({"app.py": 'print("real")\n', "broken.py": "def invalid(:\n    pass\n"})
    result = observability_opt(language="python", root=str(root))
    assert result["summary"]["partial_success"] is True
    assert result["summary"]["files_unparsed"] == ["broken.py"]
    assert [finding["subject"] for finding in result["findings"]] == ["app.py:1"]
