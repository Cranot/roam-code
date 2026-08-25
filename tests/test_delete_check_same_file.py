"""Precision regressions for same-file survivors in ``delete-check``."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from roam.db.connection import open_db
from tests.conftest import index_in_process, invoke_cli, parse_json_output

FIXTURES = Path(__file__).parent / "fixtures" / "detector_eval" / "delete-check"
CASES = {
    item["case"]: item for item in json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))["fixtures"]
}


def _delete_function(source: str, symbol: str) -> str:
    tree = ast.parse(source)
    node = next(
        item for item in tree.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == symbol
    )
    lines = source.splitlines(keepends=True)
    start = node.lineno - 1
    end = node.end_lineno
    while end < len(lines) and not lines[end].strip():
        end += 1
    return "".join(lines[:start] + lines[end:])


def _build_case(case: dict, project_factory) -> tuple[Path, Path]:
    names = case["files"] if "files" in case else [case["file"]]
    files = {f"src/{name}": (FIXTURES / name).read_text(encoding="utf-8") for name in names}
    project = project_factory(files)
    delete_from = case.get("delete_from", case.get("file"))
    source_path = project / "src" / delete_from
    source = source_path.read_text(encoding="utf-8")
    deleted = _delete_function(source, case["deleted_symbol"])
    assert deleted != source
    source_path.write_text(deleted, encoding="utf-8")
    return project, source_path


def _run_case(case: dict, cli_runner, project_factory, monkeypatch) -> dict:
    project, _source_path = _build_case(case, project_factory)
    monkeypatch.chdir(project)
    result = invoke_cli(cli_runner, ["delete-check"], cwd=project, json_mode=True)
    return parse_json_output(result, "delete-check")


def test_live_same_file_caller_makes_deletion_unsafe(cli_runner, project_factory, monkeypatch):
    """A surviving caller in the edited file must block deletion."""
    expected = CASES["same_file_live_caller"]
    data = _run_case(expected, cli_runner, project_factory, monkeypatch)

    assert data["summary"]["overall"] == expected["expected_overall"], data
    deletion = next(item for item in data["deletions"] if item["name"] == expected["deleted_symbol"])
    callers = {item["enclosing_symbol"] for item in deletion["survivors"]}
    assert set(expected["expected_callers"]) <= callers, data


def test_verify_f8_blocks_live_same_file_caller(project_factory, monkeypatch):
    """The downstream F8 gate must consume survivors from an edited file."""
    from roam.commands.cmd_verify import _check_delete_safety

    expected = CASES["same_file_live_caller"]
    project, source_path = _build_case(expected, project_factory)
    monkeypatch.chdir(project)
    output, exit_code = index_in_process(project)
    assert exit_code == 0, output

    relative_path = source_path.relative_to(project).as_posix()
    with open_db(readonly=True) as conn:
        result = _check_delete_safety(conn, [relative_path], project)

    assert result["score"] == 0, result
    assert any(
        expected["deleted_symbol"] in violation["message"] and violation["file"] == relative_path
        for violation in result["violations"]
    ), result


def test_verify_f8_preserves_safe_dead_same_file_caller(project_factory, monkeypatch):
    """F8 keeps the command's non-blocking policy for orphan callers."""
    from roam.commands.cmd_verify import _check_delete_safety

    expected = CASES["same_file_dead_caller"]
    project, source_path = _build_case(expected, project_factory)
    monkeypatch.chdir(project)
    output, exit_code = index_in_process(project)
    assert exit_code == 0, output

    relative_path = source_path.relative_to(project).as_posix()
    with open_db(readonly=True) as conn:
        result = _check_delete_safety(conn, [relative_path], project)

    assert result == {"score": 100, "violations": []}


@pytest.mark.parametrize("case_name", ["recursive_only", "unreferenced"])
def test_deletions_without_surviving_callers_are_safe(case_name, cli_runner, project_factory, monkeypatch):
    expected = CASES[case_name]
    data = _run_case(expected, cli_runner, project_factory, monkeypatch)

    assert data["summary"]["overall"] == expected["expected_overall"], data


def test_cross_file_live_caller_remains_unsafe(cli_runner, project_factory, monkeypatch):
    expected = CASES["cross_file_live_caller"]
    data = _run_case(expected, cli_runner, project_factory, monkeypatch)

    assert data["summary"]["overall"] == expected["expected_overall"], data
    deletion = next(item for item in data["deletions"] if item["name"] == expected["deleted_symbol"])
    callers = {item["enclosing_symbol"] for item in deletion["survivors"]}
    assert set(expected["expected_callers"]) <= callers, data


def test_same_file_dead_caller_preserves_non_blocking_semantics(cli_runner, project_factory, monkeypatch):
    expected = CASES["same_file_dead_caller"]
    data = _run_case(expected, cli_runner, project_factory, monkeypatch)

    assert data["summary"]["overall"] == expected["expected_overall"], data
    deletion = next(item for item in data["deletions"] if item["name"] == expected["deleted_symbol"])
    callers = {item["enclosing_symbol"] for item in deletion["survivors"]}
    assert set(expected["expected_callers"]) <= callers, data
