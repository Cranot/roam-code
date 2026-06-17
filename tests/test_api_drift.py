"""Tests for roam api-drift -- backend/frontend type contract drift detection."""

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
def drift_project(tmp_path):
    """Laravel+TS project with deliberate field mismatches."""
    proj = tmp_path / "drift_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    # PHP model
    models = proj / "app" / "Models"
    models.mkdir(parents=True)
    (models / "User.php").write_text(
        "<?php\nnamespace App\\Models;\n\n"
        "use Illuminate\\Database\\Eloquent\\Model;\n\n"
        "class User extends Model {\n"
        "    protected $fillable = ['name', 'email', 'phone', 'address'];\n"
        "    protected $hidden = ['password'];\n"
        "}\n"
    )

    # TypeScript interface (missing 'phone' and 'address', has extra 'avatar')
    types = proj / "frontend" / "types"
    types.mkdir(parents=True)
    (types / "user.ts").write_text(
        "export interface User {\n  id: number;\n  name: string;\n  email: string;\n  avatar: string;\n}\n"
    )

    git_init(proj)
    index_in_process(proj)
    return proj


@pytest.fixture
def no_drift_project(tmp_path):
    proj = tmp_path / "no_drift"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "main.py").write_text("x = 1\n")
    git_init(proj)
    index_in_process(proj)
    return proj


@pytest.fixture
def frontend_only_project(tmp_path):
    proj = tmp_path / "frontend_only"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    types = proj / "frontend" / "types"
    types.mkdir(parents=True)
    (types / "user.ts").write_text("export interface User {\n  id: number;\n  name: string;\n}\n")
    git_init(proj)
    index_in_process(proj)
    return proj


@pytest.fixture
def backend_only_project(tmp_path):
    proj = tmp_path / "backend_only"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    models = proj / "app" / "Models"
    models.mkdir(parents=True)
    (models / "User.php").write_text(
        "<?php\nnamespace App\\Models;\n\n"
        "use Illuminate\\Database\\Eloquent\\Model;\n\n"
        "class User extends Model {\n"
        "    protected $fillable = ['name', 'email'];\n"
        "}\n"
    )
    git_init(proj)
    index_in_process(proj)
    return proj


class TestApiDriftSmoke:
    def test_exits_zero(self, cli_runner, drift_project, monkeypatch):
        monkeypatch.chdir(drift_project)
        result = invoke_cli(cli_runner, ["api-drift"], cwd=drift_project)
        assert result.exit_code == 0

    def test_no_php_exits_zero(self, cli_runner, no_drift_project, monkeypatch):
        monkeypatch.chdir(no_drift_project)
        result = invoke_cli(cli_runner, ["api-drift"], cwd=no_drift_project)
        assert result.exit_code == 0


class TestApiDriftJSON:
    def test_json_envelope(self, cli_runner, drift_project, monkeypatch):
        monkeypatch.chdir(drift_project)
        result = invoke_cli(cli_runner, ["api-drift"], cwd=drift_project, json_mode=True)
        data = parse_json_output(result, "api-drift")
        assert_json_envelope(data, "api-drift")

    def test_json_summary_has_verdict(self, cli_runner, drift_project, monkeypatch):
        monkeypatch.chdir(drift_project)
        result = invoke_cli(cli_runner, ["api-drift"], cwd=drift_project, json_mode=True)
        data = parse_json_output(result, "api-drift")
        assert "findings" in data["summary"]

    def test_json_no_backend_frontend_pair_has_closed_state(self, cli_runner, no_drift_project, monkeypatch):
        monkeypatch.chdir(no_drift_project)
        result = invoke_cli(cli_runner, ["api-drift"], cwd=no_drift_project, json_mode=True)
        data = parse_json_output(result, "api-drift")

        assert_json_envelope(data, "api-drift")
        assert data["summary"]["state"] == "no_backend_frontend_pair"
        assert data["summary"]["partial_success"] is False
        assert data["unmatched"] == {"backend_only": [], "frontend_only": []}

    def test_json_no_backend_models_has_closed_state(self, cli_runner, frontend_only_project, monkeypatch):
        monkeypatch.chdir(frontend_only_project)
        result = invoke_cli(cli_runner, ["api-drift"], cwd=frontend_only_project, json_mode=True)
        data = parse_json_output(result, "api-drift")

        assert_json_envelope(data, "api-drift")
        assert data["summary"]["state"] == "no_backend_models"
        assert data["summary"]["partial_success"] is False
        assert data["unmatched"]["backend_only"] == []
        assert data["unmatched"]["frontend_only"] == ["User"]

    def test_json_no_frontend_interfaces_has_closed_state(self, cli_runner, backend_only_project, monkeypatch):
        monkeypatch.chdir(backend_only_project)
        result = invoke_cli(cli_runner, ["api-drift"], cwd=backend_only_project, json_mode=True)
        data = parse_json_output(result, "api-drift")

        assert_json_envelope(data, "api-drift")
        assert data["summary"]["state"] == "no_frontend_interfaces"
        assert data["summary"]["partial_success"] is False
        assert data["unmatched"]["backend_only"] == ["User"]
        assert data["unmatched"]["frontend_only"] == []


class TestApiDriftText:
    def test_verdict_line(self, cli_runner, drift_project, monkeypatch):
        monkeypatch.chdir(drift_project)
        result = invoke_cli(cli_runner, ["api-drift"], cwd=drift_project)
        assert "VERDICT:" in result.output
