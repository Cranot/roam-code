from __future__ import annotations

import click
import pytest
from click.testing import CliRunner

from roam.cli import cli
from tests.conftest import assert_json_envelope, git_init, index_in_process, invoke_cli, parse_json_output


@pytest.fixture
def typed_fixture_project(tmp_path):
    project = tmp_path / "typed_project"
    project.mkdir()
    (project / ".gitignore").write_text(".roam/\n")
    src = project / "src"
    src.mkdir()
    (src / "app.py").write_text(
        "from typing import Dict, Optional\n"
        "\n"
        "def missing(x):\n"
        "    return '{}'.format(x)\n"
        "\n"
        "def old_style(x: Optional[int]) -> Dict[str, int]:\n"
        "    return {'x': x or 0}\n"
        "\n"
        "def typed(x: int) -> int:\n"
        "    return x\n"
    )
    git_init(project)
    out, rc = index_in_process(project)
    assert rc == 0, out
    return project


@pytest.fixture
def non_python_project(tmp_path):
    project = tmp_path / "non_python_project"
    project.mkdir()
    (project / ".gitignore").write_text(".roam/\n")
    (project / "index.js").write_text("export function hello(name) { return name }\n")
    git_init(project)
    out, rc = index_in_process(project)
    assert rc == 0, out
    return project


def _capture_detail_kwarg(command_name: str, args: list[str]) -> dict:
    cmd = cli.get_command(None, command_name)
    assert cmd is not None
    original = cmd.callback
    seen: dict = {}

    def fake_callback(**kwargs):
        seen.update(kwargs)
        click.echo("captured")

    cmd.callback = fake_callback
    try:
        result = CliRunner().invoke(cli, [command_name, *args], catch_exceptions=False)
    finally:
        cmd.callback = original

    assert result.exit_code == 0, result.output
    return seen


@pytest.mark.parametrize(
    ("command_name", "args"),
    [
        ("py-types", ["--detail", "--top", "3"]),
        ("py-modern", ["--detail", "--top", "3"]),
        ("ai-ratio", ["--detail"]),
    ],
)
def test_command_local_detail_reaches_callback(command_name, args):
    seen = _capture_detail_kwarg(command_name, args)

    assert seen["detail"] is True


def test_py_types_local_detail_json_populates_findings(cli_runner, typed_fixture_project, monkeypatch):
    monkeypatch.chdir(typed_fixture_project)

    result = invoke_cli(
        cli_runner,
        ["py-types", "--detail", "--top", "5"],
        cwd=typed_fixture_project,
        json_mode=True,
    )
    data = parse_json_output(result, "py-types")

    assert_json_envelope(data, "py-types")
    assert data["summary"]["no_return_annotation"] > 0
    assert data["summary"]["untyped_params"] > 0
    assert len(data["findings"]) > 0


def test_py_types_agent_fact_total_public_names_symbols(cli_runner, typed_fixture_project, monkeypatch):
    monkeypatch.chdir(typed_fixture_project)

    result = invoke_cli(cli_runner, ["py-types"], cwd=typed_fixture_project, json_mode=True)
    data = parse_json_output(result, "py-types")

    facts = data["agent_contract"]["facts"]
    joined = "\n".join(facts)
    assert any("public Python callable symbols" in fact for fact in facts)
    assert "total public findings" not in joined


def test_py_types_global_detail_json_matches_local_detail(cli_runner, typed_fixture_project, monkeypatch):
    monkeypatch.chdir(typed_fixture_project)

    local_result = invoke_cli(
        cli_runner,
        ["py-types", "--detail", "--top", "5"],
        cwd=typed_fixture_project,
        json_mode=True,
    )
    global_result = invoke_cli(
        cli_runner,
        ["--detail", "py-types", "--top", "5"],
        cwd=typed_fixture_project,
        json_mode=True,
    )

    local_data = parse_json_output(local_result, "py-types")
    global_data = parse_json_output(global_result, "py-types")

    assert len(local_data["findings"]) > 0
    assert len(global_data["findings"]) == len(local_data["findings"])


def test_py_modern_local_detail_json_populates_legacy_occurrences(cli_runner, typed_fixture_project, monkeypatch):
    monkeypatch.chdir(typed_fixture_project)

    result = invoke_cli(
        cli_runner,
        ["py-modern", "--detail", "--top", "5"],
        cwd=typed_fixture_project,
        json_mode=True,
    )
    data = parse_json_output(result, "py-modern")

    assert_json_envelope(data, "py-modern")
    assert data["summary"]["legacy_typing"] > 0
    assert len(data["legacy_occurrences"]) > 0


def test_py_types_json_no_python_files_emits_envelope(cli_runner, non_python_project, monkeypatch):
    monkeypatch.chdir(non_python_project)

    result = invoke_cli(cli_runner, ["py-types"], cwd=non_python_project, json_mode=True)
    data = parse_json_output(result, "py-types")

    assert_json_envelope(data, "py-types")
    assert data["summary"]["state"] == "no_python_files"
    assert data["summary"]["partial_success"] is False
    assert data["summary"]["total_public"] == 0
