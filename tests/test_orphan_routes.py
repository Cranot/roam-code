"""Tests for roam orphan-routes -- dead Laravel API endpoint detection."""

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
    """Minimal Laravel-like project with routes and a controller."""
    proj = tmp_path / "laravel_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    # Route definitions
    routes = proj / "routes"
    routes.mkdir()
    (routes / "api.php").write_text(
        "<?php\n"
        "use Illuminate\\Support\\Facades\\Route;\n\n"
        "Route::get('/users', [UserController::class, 'index']);\n"
        "Route::post('/users', [UserController::class, 'store']);\n"
        "Route::get('/orphaned-endpoint', [OrphanController::class, 'show']);\n"
    )

    # Controller
    controllers = proj / "app" / "Http" / "Controllers"
    controllers.mkdir(parents=True)
    (controllers / "UserController.php").write_text(
        "<?php\nnamespace App\\Http\\Controllers;\n\n"
        "class UserController {\n"
        "    public function index() { return []; }\n"
        "    public function store() { return []; }\n"
        "}\n"
    )

    # Frontend that references /users but not /orphaned-endpoint
    resources = proj / "resources" / "js"
    resources.mkdir(parents=True)
    (resources / "api.js").write_text("export const fetchUsers = () => fetch('/api/users');\n")

    git_init(proj)
    index_in_process(proj)
    return proj


@pytest.fixture
def no_routes_project(tmp_path):
    """Project without any Laravel routes."""
    proj = tmp_path / "no_routes"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "main.py").write_text("x = 1\n")
    git_init(proj)
    index_in_process(proj)
    return proj


class TestOrphanRoutesSmoke:
    def test_exits_zero_with_routes(self, cli_runner, laravel_project, monkeypatch):
        monkeypatch.chdir(laravel_project)
        result = invoke_cli(cli_runner, ["orphan-routes"], cwd=laravel_project)
        assert result.exit_code == 0

    def test_exits_zero_no_routes(self, cli_runner, no_routes_project, monkeypatch):
        monkeypatch.chdir(no_routes_project)
        result = invoke_cli(cli_runner, ["orphan-routes"], cwd=no_routes_project)
        assert result.exit_code == 0


class TestOrphanRoutesJSON:
    def test_json_envelope(self, cli_runner, laravel_project, monkeypatch):
        monkeypatch.chdir(laravel_project)
        result = invoke_cli(cli_runner, ["orphan-routes"], cwd=laravel_project, json_mode=True)
        data = parse_json_output(result, "orphan-routes")
        assert_json_envelope(data, "orphan-routes")

    def test_json_summary_has_verdict(self, cli_runner, laravel_project, monkeypatch):
        monkeypatch.chdir(laravel_project)
        result = invoke_cli(cli_runner, ["orphan-routes"], cwd=laravel_project, json_mode=True)
        data = parse_json_output(result, "orphan-routes")
        assert "verdict" in data["summary"]

    def test_no_routes_json(self, cli_runner, no_routes_project, monkeypatch):
        monkeypatch.chdir(no_routes_project)
        result = invoke_cli(cli_runner, ["orphan-routes"], cwd=no_routes_project, json_mode=True)
        data = parse_json_output(result, "orphan-routes")
        assert_json_envelope(data, "orphan-routes")


class TestOrphanRoutesText:
    def test_verdict_line(self, cli_runner, laravel_project, monkeypatch):
        monkeypatch.chdir(laravel_project)
        result = invoke_cli(cli_runner, ["orphan-routes"], cwd=laravel_project)
        assert "VERDICT:" in result.output


class TestPrefixComposition:
    """``Route::prefix('x')->group(){...}`` composes child route paths so the FE
    caller of ``/x/child`` is found. Previously the prefix was dropped (``_PREFIX_RE``
    was defined but never called), so real routes were flagged orphan (measured: 50
    orphan FPs on a real Laravel app, nearly all prefix-composition artifacts).
    """

    def _paths(self, tmp_path, source: str) -> set[tuple[str, str]]:
        from roam.commands.cmd_orphan_routes import _extract_routes_from_file

        f = tmp_path / "api.php"
        f.write_text(source, encoding="utf-8")
        return {(r["method"], r["path"]) for r in _extract_routes_from_file(f)}

    def test_nested_prefix_is_composed(self, tmp_path):
        src = (
            "<?php\n"
            "Route::middleware(['auth:sanctum'])->group(function () {\n"
            "    Route::prefix('settings')->group(function () {\n"
            "        Route::get('/feature-toggles', [UserController::class, 'toggles']);\n"
            "    });\n"
            "});\n"
        )
        paths = self._paths(tmp_path, src)
        assert ("GET", "/settings/feature-toggles") in paths
        # the bare (pre-fix) path must no longer be produced
        assert ("GET", "/feature-toggles") not in paths

    def test_prefix_with_route_param_does_not_break_brace_matching(self, tmp_path):
        # The prefix string itself contains ``{cycle}``; a naive brace counter
        # would miscount and drop the whole group. String-aware matching handles it.
        src = (
            "<?php\n"
            "Route::prefix('billing-cycles/{cycle}/transfers')->group(function () {\n"
            "    Route::post('/cancel', [TransferController::class, 'cancel']);\n"
            "});\n"
        )
        paths = self._paths(tmp_path, src)
        assert ("POST", "/billing-cycles/{cycle}/transfers/cancel") in paths

    def test_double_nested_prefix(self, tmp_path):
        src = (
            "<?php\n"
            "Route::prefix('catalog')->name('catalog.')->group(function () {\n"
            "    Route::prefix('bulk-upload')->group(function () {\n"
            "        Route::post('/preview', [ImportController::class, 'preview']);\n"
            "    });\n"
            "});\n"
        )
        assert ("POST", "/catalog/bulk-upload/preview") in self._paths(tmp_path, src)

    def test_apiresource_inside_prefix_is_composed(self, tmp_path):
        src = (
            "<?php\n"
            "Route::prefix('admin')->group(function () {\n"
            "    Route::apiResource('companies', CompanyController::class);\n"
            "});\n"
        )
        paths = self._paths(tmp_path, src)
        assert ("GET", "/admin/companies") in paths
        assert ("GET", "/admin/companies/{id}") in paths

    def test_flat_route_unchanged(self, tmp_path):
        # No enclosing prefix group -> path is untouched (no regression on flat routes).
        src = "<?php\nRoute::get('/health', [HealthController::class, 'check']);\n"
        assert ("GET", "/health") in self._paths(tmp_path, src)
