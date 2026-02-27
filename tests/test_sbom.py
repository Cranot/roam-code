"""Tests for roam sbom -- Software Bill of Materials generation."""

from __future__ import annotations

import json

import pytest

from tests.conftest import (
    assert_json_envelope,
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)


@pytest.fixture
def sbom_project(tmp_path):
    """Project with a requirements.txt for dependency discovery."""
    proj = tmp_path / "sbom_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "main.py").write_text("import requests\n\ndef fetch(url):\n    return requests.get(url)\n")
    (proj / "requirements.txt").write_text("requests==2.31.0\nclick>=8.0\n")
    git_init(proj)
    index_in_process(proj)
    return proj


@pytest.fixture
def sbom_project_no_deps(tmp_path):
    """Project with no dependency manifests."""
    proj = tmp_path / "sbom_empty"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "main.py").write_text("x = 1\n")
    git_init(proj)
    index_in_process(proj)
    return proj


class TestSbomSmoke:
    def test_exits_zero(self, cli_runner, sbom_project, monkeypatch):
        monkeypatch.chdir(sbom_project)
        result = invoke_cli(cli_runner, ["sbom"], cwd=sbom_project)
        assert result.exit_code == 0

    def test_no_deps_exits_zero(self, cli_runner, sbom_project_no_deps, monkeypatch):
        monkeypatch.chdir(sbom_project_no_deps)
        result = invoke_cli(cli_runner, ["sbom"], cwd=sbom_project_no_deps)
        assert result.exit_code == 0

    def test_no_reachability_flag(self, cli_runner, sbom_project, monkeypatch):
        monkeypatch.chdir(sbom_project)
        result = invoke_cli(cli_runner, ["sbom", "--no-reachability"], cwd=sbom_project)
        assert result.exit_code == 0

    def test_spdx_format(self, cli_runner, sbom_project, monkeypatch):
        monkeypatch.chdir(sbom_project)
        result = invoke_cli(cli_runner, ["sbom", "--format", "spdx"], cwd=sbom_project)
        assert result.exit_code == 0


class TestSbomJSON:
    def test_json_envelope(self, cli_runner, sbom_project, monkeypatch):
        monkeypatch.chdir(sbom_project)
        result = invoke_cli(cli_runner, ["sbom"], cwd=sbom_project, json_mode=True)
        data = parse_json_output(result, "sbom")
        assert_json_envelope(data, "sbom")

    def test_json_summary_has_verdict(self, cli_runner, sbom_project, monkeypatch):
        monkeypatch.chdir(sbom_project)
        result = invoke_cli(cli_runner, ["sbom"], cwd=sbom_project, json_mode=True)
        data = parse_json_output(result, "sbom")
        assert "verdict" in data["summary"]

    def test_json_has_sbom_data(self, cli_runner, sbom_project, monkeypatch):
        monkeypatch.chdir(sbom_project)
        result = invoke_cli(cli_runner, ["sbom"], cwd=sbom_project, json_mode=True)
        data = parse_json_output(result, "sbom")
        # Should contain SBOM document
        assert (
            "sbom" in data or "document" in data or "components" in data["summary"] or "dependencies" in data["summary"]
        )


class TestSbomText:
    def test_verdict_line(self, cli_runner, sbom_project, monkeypatch):
        monkeypatch.chdir(sbom_project)
        result = invoke_cli(cli_runner, ["sbom"], cwd=sbom_project)
        assert "VERDICT:" in result.output

    def test_output_contains_dependency(self, cli_runner, sbom_project, monkeypatch):
        monkeypatch.chdir(sbom_project)
        result = invoke_cli(cli_runner, ["sbom"], cwd=sbom_project)
        assert "requests" in result.output.lower()


class TestSbomOutputFile:
    def test_write_to_file(self, cli_runner, sbom_project, monkeypatch):
        monkeypatch.chdir(sbom_project)
        out_path = sbom_project / "sbom.json"
        result = invoke_cli(cli_runner, ["sbom", "-o", str(out_path)], cwd=sbom_project)
        assert result.exit_code == 0
        if out_path.exists():
            content = json.loads(out_path.read_text(encoding="utf-8"))
            assert isinstance(content, dict)
