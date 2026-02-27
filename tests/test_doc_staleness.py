"""Tests for roam doc-staleness -- stale docstring detection."""

from __future__ import annotations

import pytest

from tests.conftest import (
    assert_json_envelope,
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)


@pytest.fixture
def staleness_project(tmp_path):
    """Project with a docstring that might go stale."""
    proj = tmp_path / "stale_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    (proj / "module.py").write_text('def greet(name):\n    """Say hello to the user."""\n    return f"Hello, {name}"\n')
    git_init(proj)
    index_in_process(proj)
    return proj


class TestDocStalenessSmoke:
    def test_exits_zero(self, cli_runner, staleness_project, monkeypatch):
        monkeypatch.chdir(staleness_project)
        result = invoke_cli(cli_runner, ["doc-staleness"], cwd=staleness_project)
        assert result.exit_code == 0

    def test_with_days(self, cli_runner, staleness_project, monkeypatch):
        monkeypatch.chdir(staleness_project)
        result = invoke_cli(cli_runner, ["doc-staleness", "--days", "1"], cwd=staleness_project)
        assert result.exit_code == 0

    def test_empty_project(self, cli_runner, tmp_path, monkeypatch):
        proj = tmp_path / "empty"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")
        (proj / "x.py").write_text("x = 1\n")
        git_init(proj)
        index_in_process(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["doc-staleness"], cwd=proj)
        assert result.exit_code == 0


class TestDocStalenessJSON:
    def test_json_envelope(self, cli_runner, staleness_project, monkeypatch):
        monkeypatch.chdir(staleness_project)
        result = invoke_cli(cli_runner, ["doc-staleness"], cwd=staleness_project, json_mode=True)
        data = parse_json_output(result, "doc-staleness")
        assert_json_envelope(data, "doc-staleness")

    def test_json_summary_has_verdict(self, cli_runner, staleness_project, monkeypatch):
        monkeypatch.chdir(staleness_project)
        result = invoke_cli(cli_runner, ["doc-staleness"], cwd=staleness_project, json_mode=True)
        data = parse_json_output(result, "doc-staleness")
        assert "verdict" in data["summary"]


class TestDocStalenessText:
    def test_verdict_line(self, cli_runner, staleness_project, monkeypatch):
        monkeypatch.chdir(staleness_project)
        result = invoke_cli(cli_runner, ["doc-staleness"], cwd=staleness_project)
        assert "VERDICT:" in result.output
