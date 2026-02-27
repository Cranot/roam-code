"""Tests for roam adrs -- Architecture Decision Record discovery."""

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
def adr_project(tmp_path):
    proj = tmp_path / "adr_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    # Source files
    src = proj / "src"
    src.mkdir()
    (src / "auth.py").write_text("def authenticate(user, password):\n    return user if password else None\n")
    (src / "db.py").write_text("def connect():\n    return 'connection'\n")

    # ADR directory with 3 ADRs
    adr_dir = proj / "docs" / "adr"
    adr_dir.mkdir(parents=True)
    (adr_dir / "0001-use-sqlite.md").write_text(
        "# ADR 1: Use SQLite\n\n"
        "## Status\n\nAccepted\n\n"
        "## Context\n\nWe need a local database.\n\n"
        "## Decision\n\nUse SQLite via `src/db.py`.\n"
    )
    (adr_dir / "0002-jwt-auth.md").write_text(
        "# ADR 2: JWT Authentication\n\n"
        "## Status\n\nProposed\n\n"
        "## Context\n\nNeed auth for API.\n\n"
        "## Decision\n\nUse JWT tokens in `src/auth.py`.\n"
    )
    (adr_dir / "0003-deprecated-xml.md").write_text(
        "# ADR 3: Drop XML Support\n\n## Status\n\nDeprecated\n\n## Context\n\nXML is no longer used.\n"
    )

    git_init(proj)
    index_in_process(proj)
    return proj


class TestAdrsSmoke:
    def test_exits_zero(self, cli_runner, adr_project, monkeypatch):
        monkeypatch.chdir(adr_project)
        result = invoke_cli(cli_runner, ["adrs"], cwd=adr_project)
        assert result.exit_code == 0

    def test_no_adrs_project(self, cli_runner, tmp_path, monkeypatch):
        proj = tmp_path / "empty"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")
        (proj / "main.py").write_text("x = 1\n")
        git_init(proj)
        index_in_process(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["adrs"], cwd=proj)
        assert result.exit_code == 0


class TestAdrsJSON:
    def test_json_envelope(self, cli_runner, adr_project, monkeypatch):
        monkeypatch.chdir(adr_project)
        result = invoke_cli(cli_runner, ["adrs"], cwd=adr_project, json_mode=True)
        data = parse_json_output(result, "adrs")
        assert_json_envelope(data, "adrs")

    def test_json_has_adrs_list(self, cli_runner, adr_project, monkeypatch):
        monkeypatch.chdir(adr_project)
        result = invoke_cli(cli_runner, ["adrs"], cwd=adr_project, json_mode=True)
        data = parse_json_output(result, "adrs")
        assert "adrs" in data
        assert len(data["adrs"]) == 3

    def test_adr_entries_have_expected_fields(self, cli_runner, adr_project, monkeypatch):
        monkeypatch.chdir(adr_project)
        result = invoke_cli(cli_runner, ["adrs"], cwd=adr_project, json_mode=True)
        data = parse_json_output(result, "adrs")
        for adr in data["adrs"]:
            assert "title" in adr
            assert "status" in adr
            assert "file" in adr or "path" in adr


class TestAdrsText:
    def test_verdict_line(self, cli_runner, adr_project, monkeypatch):
        monkeypatch.chdir(adr_project)
        result = invoke_cli(cli_runner, ["adrs"], cwd=adr_project)
        assert "VERDICT:" in result.output

    def test_shows_adr_titles(self, cli_runner, adr_project, monkeypatch):
        monkeypatch.chdir(adr_project)
        result = invoke_cli(cli_runner, ["adrs"], cwd=adr_project)
        assert "SQLite" in result.output or "sqlite" in result.output.lower()

    def test_status_filter(self, cli_runner, adr_project, monkeypatch):
        monkeypatch.chdir(adr_project)
        result = invoke_cli(cli_runner, ["adrs", "--status", "accepted"], cwd=adr_project)
        assert result.exit_code == 0
