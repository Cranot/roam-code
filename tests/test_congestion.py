"""Tests for roam congestion -- developer congestion detection."""

from __future__ import annotations

import json
import os
import subprocess
import pytest

from tests.conftest import (
    assert_json_envelope,
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)


@pytest.fixture
def congestion_project(tmp_path):
    """Project with multiple authors editing the same file."""
    proj = tmp_path / "cong_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    (proj / "hot_file.py").write_text(
        "def process():\n"
        "    return 1\n"
    )
    (proj / "cold_file.py").write_text(
        "def helper():\n"
        "    return 2\n"
    )
    git_init(proj)

    # Simulate second author editing hot_file
    subprocess.run(["git", "config", "user.name", "Dev2"], cwd=proj, capture_output=True)
    subprocess.run(["git", "config", "user.email", "dev2@test.com"], cwd=proj, capture_output=True)
    (proj / "hot_file.py").write_text(
        "def process():\n"
        "    return 1\n\ndef added_by_dev2():\n    pass\n"
    )
    subprocess.run(["git", "add", "."], cwd=proj, capture_output=True)
    subprocess.run(["git", "commit", "-m", "dev2 changes"], cwd=proj, capture_output=True)

    # Third author
    subprocess.run(["git", "config", "user.name", "Dev3"], cwd=proj, capture_output=True)
    subprocess.run(["git", "config", "user.email", "dev3@test.com"], cwd=proj, capture_output=True)
    (proj / "hot_file.py").write_text(
        "def process():\n"
        "    return 1\n\ndef added_by_dev2():\n    pass\n\ndef added_by_dev3():\n    pass\n"
    )
    subprocess.run(["git", "add", "."], cwd=proj, capture_output=True)
    subprocess.run(["git", "commit", "-m", "dev3 changes"], cwd=proj, capture_output=True)

    index_in_process(proj)
    return proj


class TestCongestionSmoke:
    def test_exits_zero(self, cli_runner, congestion_project, monkeypatch):
        monkeypatch.chdir(congestion_project)
        result = invoke_cli(cli_runner, ["congestion"], cwd=congestion_project)
        assert result.exit_code == 0

    def test_with_window_option(self, cli_runner, congestion_project, monkeypatch):
        monkeypatch.chdir(congestion_project)
        result = invoke_cli(cli_runner, ["congestion", "--window", "30"], cwd=congestion_project)
        assert result.exit_code == 0


class TestCongestionJSON:
    def test_json_envelope(self, cli_runner, congestion_project, monkeypatch):
        monkeypatch.chdir(congestion_project)
        result = invoke_cli(cli_runner, ["congestion"], cwd=congestion_project, json_mode=True)
        data = parse_json_output(result, "congestion")
        assert_json_envelope(data, "congestion")

    def test_json_summary_has_verdict(self, cli_runner, congestion_project, monkeypatch):
        monkeypatch.chdir(congestion_project)
        result = invoke_cli(cli_runner, ["congestion"], cwd=congestion_project, json_mode=True)
        data = parse_json_output(result, "congestion")
        assert "verdict" in data["summary"]


class TestCongestionText:
    def test_verdict_line(self, cli_runner, congestion_project, monkeypatch):
        monkeypatch.chdir(congestion_project)
        result = invoke_cli(cli_runner, ["congestion"], cwd=congestion_project)
        assert "VERDICT:" in result.output

    def test_empty_project(self, cli_runner, tmp_path, monkeypatch):
        proj = tmp_path / "empty"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")
        (proj / "x.py").write_text("x = 1\n")
        git_init(proj)
        index_in_process(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["congestion"], cwd=proj)
        assert result.exit_code == 0
