"""Tests for roam fn-coupling -- function-level temporal coupling detection."""

from __future__ import annotations

import pytest

from tests.conftest import (
    assert_json_envelope,
    git_commit,
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)


@pytest.fixture
def coupling_project(tmp_path):
    """Project where two functions in different files co-change."""
    proj = tmp_path / "coupling_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    (proj / "auth.py").write_text("def login(user):\n    return True\n")
    (proj / "audit.py").write_text("def log_access(user):\n    print(user)\n")
    git_init(proj)

    # Second commit: change both files together
    (proj / "auth.py").write_text("def login(user):\n    return user is not None\n")
    (proj / "audit.py").write_text("def log_access(user):\n    print(f'Access: {user}')\n")
    git_commit(proj, "update both")

    # Third commit: change both again
    (proj / "auth.py").write_text("def login(user):\n    return user is not None and len(user) > 0\n")
    (proj / "audit.py").write_text("def log_access(user):\n    print(f'Access granted: {user}')\n")
    git_commit(proj, "update both again")

    index_in_process(proj)
    return proj


class TestFnCouplingSmoke:
    def test_exits_zero(self, cli_runner, coupling_project, monkeypatch):
        monkeypatch.chdir(coupling_project)
        result = invoke_cli(cli_runner, ["fn-coupling"], cwd=coupling_project)
        assert result.exit_code == 0

    def test_with_min_count(self, cli_runner, coupling_project, monkeypatch):
        monkeypatch.chdir(coupling_project)
        result = invoke_cli(cli_runner, ["fn-coupling", "--min-count", "1"], cwd=coupling_project)
        assert result.exit_code == 0

    def test_include_connected(self, cli_runner, coupling_project, monkeypatch):
        monkeypatch.chdir(coupling_project)
        result = invoke_cli(cli_runner, ["fn-coupling", "--include-connected"], cwd=coupling_project)
        assert result.exit_code == 0


class TestFnCouplingJSON:
    def test_json_envelope(self, cli_runner, coupling_project, monkeypatch):
        monkeypatch.chdir(coupling_project)
        result = invoke_cli(cli_runner, ["fn-coupling"], cwd=coupling_project, json_mode=True)
        data = parse_json_output(result, "fn-coupling")
        assert_json_envelope(data, "fn-coupling")

    def test_json_summary_has_verdict(self, cli_runner, coupling_project, monkeypatch):
        monkeypatch.chdir(coupling_project)
        result = invoke_cli(cli_runner, ["fn-coupling"], cwd=coupling_project, json_mode=True)
        data = parse_json_output(result, "fn-coupling")
        assert "verdict" in data["summary"]


class TestFnCouplingText:
    def test_verdict_line(self, cli_runner, coupling_project, monkeypatch):
        monkeypatch.chdir(coupling_project)
        result = invoke_cli(cli_runner, ["fn-coupling"], cwd=coupling_project)
        assert "VERDICT:" in result.output

    def test_empty_project(self, cli_runner, tmp_path, monkeypatch):
        proj = tmp_path / "empty"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")
        (proj / "x.py").write_text("x = 1\n")
        git_init(proj)
        index_in_process(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["fn-coupling"], cwd=proj)
        assert result.exit_code == 0
