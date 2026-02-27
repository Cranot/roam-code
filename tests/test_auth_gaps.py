"""Tests for roam auth-gaps -- find endpoints missing authentication/authorization."""

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
def laravel_project(tmp_path):
    """Laravel-style PHP project with routes and controllers."""
    proj = tmp_path / "laravel_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    # routes/web.php: one auth-protected route, two public routes
    routes_dir = proj / "routes"
    routes_dir.mkdir()
    (routes_dir / "web.php").write_text(
        "<?php\n"
        "use Illuminate\\Support\\Facades\\Route;\n"
        "\n"
        "Route::middleware('auth')->group(function () {\n"
        "    Route::get('/dashboard', [DashboardController::class, 'index']);\n"
        "});\n"
        "\n"
        "Route::get('/public', [PublicController::class, 'index']);\n"
        "Route::post('/api/data', [ApiController::class, 'store']);\n"
    )

    # app/Http/Controllers/DashboardController.php: protected controller
    controllers_dir = proj / "app" / "Http" / "Controllers"
    controllers_dir.mkdir(parents=True)
    (controllers_dir / "DashboardController.php").write_text(
        "<?php\n"
        "\n"
        "namespace App\\Http\\Controllers;\n"
        "\n"
        "class DashboardController extends Controller\n"
        "{\n"
        "    public function index()\n"
        "    {\n"
        "        return view('dashboard');\n"
        "    }\n"
        "}\n"
    )

    # app/Http/Controllers/ApiController.php: CRUD methods without auth checks
    (controllers_dir / "ApiController.php").write_text(
        "<?php\n"
        "\n"
        "namespace App\\Http\\Controllers;\n"
        "\n"
        "class ApiController extends Controller\n"
        "{\n"
        "    public function store()\n"
        "    {\n"
        "        return response()->json(['status' => 'created'], 201);\n"
        "    }\n"
        "\n"
        "    public function index()\n"
        "    {\n"
        "        return response()->json([]);\n"
        "    }\n"
        "}\n"
    )

    # app/Http/Controllers/PublicController.php: intentionally public
    (controllers_dir / "PublicController.php").write_text(
        "<?php\n"
        "\n"
        "namespace App\\Http\\Controllers;\n"
        "\n"
        "class PublicController extends Controller\n"
        "{\n"
        "    public function index()\n"
        "    {\n"
        "        return view('public');\n"
        "    }\n"
        "}\n"
    )

    git_init(proj)
    index_in_process(proj)
    return proj


@pytest.fixture
def non_php_project(tmp_path):
    """Pure Python project with no PHP files."""
    proj = tmp_path / "python_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text(
        'def main():\n    """Main entry point."""\n    return \'hello\'\n\n\ndef helper():\n    return 42\n'
    )
    git_init(proj)
    index_in_process(proj)
    return proj


class TestAuthGapsSmoke:
    def test_exits_zero_on_php_project(self, cli_runner, laravel_project, monkeypatch):
        monkeypatch.chdir(laravel_project)
        result = invoke_cli(cli_runner, ["auth-gaps"], cwd=laravel_project)
        assert result.exit_code == 0

    def test_exits_zero_on_non_php_project(self, cli_runner, non_php_project, monkeypatch):
        monkeypatch.chdir(non_php_project)
        result = invoke_cli(cli_runner, ["auth-gaps"], cwd=non_php_project)
        assert result.exit_code == 0

    def test_routes_only_flag_exits_zero(self, cli_runner, laravel_project, monkeypatch):
        monkeypatch.chdir(laravel_project)
        result = invoke_cli(cli_runner, ["auth-gaps", "--routes-only"], cwd=laravel_project)
        assert result.exit_code == 0

    def test_controllers_only_flag_exits_zero(self, cli_runner, laravel_project, monkeypatch):
        monkeypatch.chdir(laravel_project)
        result = invoke_cli(cli_runner, ["auth-gaps", "--controllers-only"], cwd=laravel_project)
        assert result.exit_code == 0

    def test_min_confidence_high_exits_zero(self, cli_runner, laravel_project, monkeypatch):
        monkeypatch.chdir(laravel_project)
        result = invoke_cli(cli_runner, ["auth-gaps", "--min-confidence", "high"], cwd=laravel_project)
        assert result.exit_code == 0


class TestAuthGapsJSON:
    def test_json_envelope(self, cli_runner, laravel_project, monkeypatch):
        monkeypatch.chdir(laravel_project)
        result = invoke_cli(cli_runner, ["auth-gaps"], cwd=laravel_project, json_mode=True)
        data = parse_json_output(result, "auth-gaps")
        assert_json_envelope(data, "auth-gaps")

    def test_json_summary_has_verdict(self, cli_runner, laravel_project, monkeypatch):
        monkeypatch.chdir(laravel_project)
        result = invoke_cli(cli_runner, ["auth-gaps"], cwd=laravel_project, json_mode=True)
        data = parse_json_output(result, "auth-gaps")
        assert "verdict" in data["summary"]

    def test_json_summary_has_counts(self, cli_runner, laravel_project, monkeypatch):
        monkeypatch.chdir(laravel_project)
        result = invoke_cli(cli_runner, ["auth-gaps"], cwd=laravel_project, json_mode=True)
        data = parse_json_output(result, "auth-gaps")
        summary = data["summary"]
        assert "total" in summary
        assert "high" in summary
        assert "medium" in summary
        assert "low" in summary

    def test_json_has_route_gaps_field(self, cli_runner, laravel_project, monkeypatch):
        monkeypatch.chdir(laravel_project)
        result = invoke_cli(cli_runner, ["auth-gaps"], cwd=laravel_project, json_mode=True)
        data = parse_json_output(result, "auth-gaps")
        assert "route_gaps" in data
        assert isinstance(data["route_gaps"], list)

    def test_json_has_controller_gaps_field(self, cli_runner, laravel_project, monkeypatch):
        monkeypatch.chdir(laravel_project)
        result = invoke_cli(cli_runner, ["auth-gaps"], cwd=laravel_project, json_mode=True)
        data = parse_json_output(result, "auth-gaps")
        assert "controller_gaps" in data
        assert isinstance(data["controller_gaps"], list)

    def test_json_detects_unprotected_routes(self, cli_runner, laravel_project, monkeypatch):
        monkeypatch.chdir(laravel_project)
        result = invoke_cli(cli_runner, ["auth-gaps"], cwd=laravel_project, json_mode=True)
        data = parse_json_output(result, "auth-gaps")
        # /public and /api/data are outside the auth group, so should appear as gaps
        route_paths = [gap.get("path", "") for gap in data["route_gaps"]]
        assert any("/public" in p or "/api/data" in p for p in route_paths)

    def test_non_php_project_json_envelope(self, cli_runner, non_php_project, monkeypatch):
        monkeypatch.chdir(non_php_project)
        result = invoke_cli(cli_runner, ["auth-gaps"], cwd=non_php_project, json_mode=True)
        data = parse_json_output(result, "auth-gaps")
        assert_json_envelope(data, "auth-gaps")

    def test_non_php_project_has_zero_total(self, cli_runner, non_php_project, monkeypatch):
        monkeypatch.chdir(non_php_project)
        result = invoke_cli(cli_runner, ["auth-gaps"], cwd=non_php_project, json_mode=True)
        data = parse_json_output(result, "auth-gaps")
        assert data["summary"]["total"] == 0


class TestAuthGapsText:
    def test_verdict_line(self, cli_runner, laravel_project, monkeypatch):
        monkeypatch.chdir(laravel_project)
        result = invoke_cli(cli_runner, ["auth-gaps"], cwd=laravel_project)
        assert "VERDICT:" in result.output

    def test_verdict_line_on_non_php_project(self, cli_runner, non_php_project, monkeypatch):
        monkeypatch.chdir(non_php_project)
        result = invoke_cli(cli_runner, ["auth-gaps"], cwd=non_php_project)
        assert "VERDICT:" in result.output

    def test_output_mentions_auth_gaps_header(self, cli_runner, laravel_project, monkeypatch):
        monkeypatch.chdir(laravel_project)
        result = invoke_cli(cli_runner, ["auth-gaps"], cwd=laravel_project)
        assert "Auth Gaps" in result.output or "auth gap" in result.output.lower()

    def test_protected_route_not_flagged(self, cli_runner, laravel_project, monkeypatch):
        """The /dashboard route inside middleware('auth') group must NOT be flagged."""
        monkeypatch.chdir(laravel_project)
        result = invoke_cli(cli_runner, ["auth-gaps"], cwd=laravel_project, json_mode=True)
        data = parse_json_output(result, "auth-gaps")
        route_paths = [gap.get("path", "") for gap in data["route_gaps"]]
        assert "/dashboard" not in route_paths

    def test_routes_only_skips_controller_section(self, cli_runner, laravel_project, monkeypatch):
        monkeypatch.chdir(laravel_project)
        result = invoke_cli(cli_runner, ["auth-gaps", "--routes-only"], cwd=laravel_project, json_mode=True)
        data = parse_json_output(result, "auth-gaps")
        # With --routes-only, controller_gaps should be empty
        assert data["controller_gaps"] == []

    def test_controllers_only_skips_route_section(self, cli_runner, laravel_project, monkeypatch):
        monkeypatch.chdir(laravel_project)
        result = invoke_cli(cli_runner, ["auth-gaps", "--controllers-only"], cwd=laravel_project, json_mode=True)
        data = parse_json_output(result, "auth-gaps")
        # With --controllers-only, route_gaps should be empty
        assert data["route_gaps"] == []
