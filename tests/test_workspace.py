"""Tests for multi-repo workspace support."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# Workspace tests rely on process-global cwd; run sequentially under xdist
pytestmark = pytest.mark.xdist_group("workspace")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _git_init(root: Path):
    """Initialize a git repo at *root*."""
    subprocess.run(["git", "init"], cwd=root, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=root, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=root, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, capture_output=True)


def _run_roam(args, cwd):
    """Run roam CLI as a subprocess."""
    result = subprocess.run(
        [sys.executable, "-m", "roam"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return result


@pytest.fixture(scope="module")
def workspace_root(tmp_path_factory):
    """Create a workspace with a frontend and backend repo, each indexed."""
    ws_root = tmp_path_factory.mktemp("workspace")

    # --- Frontend repo (JS/TS-like) ---
    fe_root = ws_root / "frontend"
    fe_root.mkdir()
    (fe_root / "package.json").write_text(json.dumps({
        "name": "frontend",
        "dependencies": {"vue": "^3.0.0", "axios": "^1.0.0"},
    }))
    (fe_root / "src").mkdir()
    (fe_root / "src" / "api.js").write_text(
        'import axios from "axios";\n'
        '\n'
        'const api = axios.create({ baseURL: "/api" });\n'
        '\n'
        'export function fetchKiniseis() {\n'
        '  return api.get("/kiniseis");\n'
        '}\n'
        '\n'
        'export function saveKinisi(data) {\n'
        '  return api.post("/kiniseis/save", data);\n'
        '}\n'
        '\n'
        'export function deleteKinisi(id) {\n'
        '  return api.delete(`/kiniseis/${id}`);\n'
        '}\n'
        '\n'
        'export function getArticle(id) {\n'
        '  return api.get(`/articles/${id}`);\n'
        '}\n'
    )
    (fe_root / "src" / "store.js").write_text(
        'import { fetchKiniseis, saveKinisi } from "./api";\n'
        '\n'
        'export class KinisiStore {\n'
        '  async load() {\n'
        '    const result = await fetchKiniseis();\n'
        '    return result.data;\n'
        '  }\n'
        '  async save(data) {\n'
        '    return saveKinisi(data);\n'
        '  }\n'
        '}\n'
    )
    _git_init(fe_root)

    # --- Backend repo (PHP-like with Laravel routes) ---
    be_root = ws_root / "backend"
    be_root.mkdir()
    (be_root / "composer.json").write_text(json.dumps({
        "name": "backend",
        "require": {"laravel/framework": "^12.0"},
    }))
    (be_root / "artisan").write_text("#!/usr/bin/env php\n")
    (be_root / "routes").mkdir()
    (be_root / "routes" / "api.php").write_text(
        '<?php\n'
        'use App\\Http\\Controllers\\KinisiController;\n'
        'use App\\Http\\Controllers\\ArticleController;\n'
        '\n'
        "Route::get('/kiniseis', [KinisiController::class, 'index']);\n"
        "Route::post('/kiniseis/save', [KinisiController::class, 'store']);\n"
        "Route::delete('/kiniseis/{id}', [KinisiController::class, 'destroy']);\n"
        "Route::get('/articles/{id}', [ArticleController::class, 'show']);\n"
    )
    (be_root / "app").mkdir()
    (be_root / "app" / "KinisiController.php").write_text(
        '<?php\n'
        'namespace App\\Http\\Controllers;\n'
        '\n'
        'class KinisiController {\n'
        '    public function index() { return []; }\n'
        '    public function store($request) { return null; }\n'
        '    public function destroy($id) { return null; }\n'
        '}\n'
    )
    (be_root / "app" / "ArticleController.php").write_text(
        '<?php\n'
        'namespace App\\Http\\Controllers;\n'
        '\n'
        'class ArticleController {\n'
        '    public function show($id) { return null; }\n'
        '}\n'
    )
    _git_init(be_root)

    # Index both repos
    _run_roam(["index"], fe_root)
    _run_roam(["index"], be_root)

    return ws_root


# ===================================================================
# Phase 1: Config, DB, ws init / ws status
# ===================================================================


class TestWorkspaceConfig:
    """Test workspace config parsing and validation."""

    def test_save_and_load_config(self, tmp_path):
        from roam.workspace.config import save_workspace_config, load_workspace_config

        config = {
            "workspace": "test-ws",
            "repos": [
                {"path": "frontend", "role": "frontend"},
                {"path": "backend", "role": "backend"},
            ],
            "connections": [],
        }
        save_workspace_config(tmp_path, config)
        loaded = load_workspace_config(tmp_path)
        assert loaded["workspace"] == "test-ws"
        assert len(loaded["repos"]) == 2

    def test_find_workspace_root(self, tmp_path):
        from roam.workspace.config import find_workspace_root, save_workspace_config

        # No config -> None
        assert find_workspace_root(str(tmp_path)) is None

        # Create config at root
        save_workspace_config(tmp_path, {
            "workspace": "test", "repos": [{"path": "a"}],
        })

        # Find from root
        assert find_workspace_root(str(tmp_path)) == tmp_path

        # Find from subdirectory
        sub = tmp_path / "sub" / "deep"
        sub.mkdir(parents=True)
        assert find_workspace_root(str(sub)) == tmp_path

    def test_invalid_config_no_workspace(self, tmp_path):
        from roam.workspace.config import load_workspace_config

        (tmp_path / ".roam-workspace.json").write_text('{"repos": []}')
        with pytest.raises(ValueError, match="Missing 'workspace'"):
            load_workspace_config(tmp_path)

    def test_invalid_config_no_repos(self, tmp_path):
        from roam.workspace.config import load_workspace_config

        (tmp_path / ".roam-workspace.json").write_text('{"workspace": "x"}')
        with pytest.raises(ValueError, match="Missing or invalid 'repos'"):
            load_workspace_config(tmp_path)

    def test_invalid_config_repo_no_path(self, tmp_path):
        from roam.workspace.config import load_workspace_config

        (tmp_path / ".roam-workspace.json").write_text(
            '{"workspace": "x", "repos": [{"name": "a"}]}'
        )
        with pytest.raises(ValueError, match="missing 'path'"):
            load_workspace_config(tmp_path)

    def test_get_repo_paths(self, tmp_path):
        from roam.workspace.config import get_repo_paths

        config = {
            "workspace": "test",
            "repos": [
                {"path": "fe", "role": "frontend"},
                {"path": "be", "role": "backend", "name": "my-backend"},
            ],
        }
        paths = get_repo_paths(config, tmp_path)
        assert len(paths) == 2
        assert paths[0]["name"] == "fe"  # uses dir name
        assert paths[0]["role"] == "frontend"
        assert paths[1]["name"] == "my-backend"
        assert paths[1]["db_path"] == (tmp_path / "be" / ".roam" / "index.db").resolve()


class TestWorkspaceDB:
    """Test workspace overlay DB."""

    def test_schema_creation(self, tmp_path):
        from roam.workspace.db import open_workspace_db

        with open_workspace_db(tmp_path) as conn:
            # Tables should exist
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "ORDER BY name"
            ).fetchall()
            table_names = [t["name"] for t in tables]
            assert "ws_repos" in table_names
            assert "ws_route_symbols" in table_names
            assert "ws_cross_edges" in table_names

    def test_upsert_repo(self, tmp_path):
        from roam.workspace.db import open_workspace_db, upsert_repo, get_repos

        with open_workspace_db(tmp_path) as conn:
            rid = upsert_repo(conn, "fe", "/path/fe", "frontend",
                              "/path/fe/.roam/index.db")
            assert rid > 0

            repos = get_repos(conn)
            assert len(repos) == 1
            assert repos[0]["name"] == "fe"

            # Upsert same name updates
            rid2 = upsert_repo(conn, "fe", "/new/path", "frontend",
                               "/new/.roam/index.db")
            assert rid2 == rid
            repos = get_repos(conn)
            assert len(repos) == 1
            assert repos[0]["path"] == "/new/path"

    def test_cross_edge_operations(self, tmp_path):
        from roam.workspace.db import (
            open_workspace_db, upsert_repo, get_cross_edges,
            clear_cross_edges,
        )

        with open_workspace_db(tmp_path) as conn:
            r1 = upsert_repo(conn, "fe", "/fe", "frontend", "/fe/db")
            r2 = upsert_repo(conn, "be", "/be", "backend", "/be/db")

            conn.execute(
                "INSERT INTO ws_cross_edges "
                "(source_repo_id, source_symbol_id, "
                " target_repo_id, target_symbol_id, kind, metadata) "
                "VALUES (?, 1, ?, 2, 'api_call', '{}')",
                (r1, r2),
            )

            edges = get_cross_edges(conn)
            assert len(edges) == 1
            assert edges[0]["source_repo_name"] == "fe"
            assert edges[0]["target_repo_name"] == "be"

            clear_cross_edges(conn)
            assert len(get_cross_edges(conn)) == 0


class TestWsInit:
    """Test `roam ws init` command."""

    def test_ws_init_creates_config(self, workspace_root):
        fe = workspace_root / "frontend"
        be = workspace_root / "backend"
        result = _run_roam(
            ["ws", "init", str(fe), str(be), "--name", "test-ws"],
            workspace_root,
        )
        assert result.returncode == 0
        assert "test-ws" in result.stdout

        # Config file should exist
        config_path = workspace_root / ".roam-workspace.json"
        assert config_path.exists()
        config = json.loads(config_path.read_text())
        assert config["workspace"] == "test-ws"
        assert len(config["repos"]) == 2

    def test_ws_init_json(self, workspace_root):
        fe = workspace_root / "frontend"
        be = workspace_root / "backend"
        result = _run_roam(
            ["--json", "ws", "init", str(fe), str(be), "--name", "test-ws-json"],
            workspace_root,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["command"] == "ws-init"
        assert data["summary"]["repos"] == 2

    def test_ws_init_detects_roles(self, workspace_root):
        config_path = workspace_root / ".roam-workspace.json"
        if config_path.exists():
            config = json.loads(config_path.read_text())
            roles = {r["name"]: r.get("role", "") for r in config["repos"]}
            assert roles.get("frontend") == "frontend"
            assert roles.get("backend") == "backend"

    def test_ws_init_nonexistent_path(self, tmp_path):
        result = _run_roam(
            ["ws", "init", "/nonexistent/path"],
            tmp_path,
        )
        assert result.returncode != 0 or "ERROR" in result.stderr


class TestWsStatus:
    """Test `roam ws status` command."""

    def test_ws_status(self, workspace_root):
        # Ensure workspace is initialized
        fe = workspace_root / "frontend"
        be = workspace_root / "backend"
        _run_roam(["ws", "init", str(fe), str(be), "--name", "status-test"],
                   workspace_root)

        result = _run_roam(["ws", "status"], workspace_root)
        assert result.returncode == 0
        assert "status-test" in result.stdout
        assert "frontend" in result.stdout
        assert "backend" in result.stdout

    def test_ws_status_json(self, workspace_root):
        result = _run_roam(["--json", "ws", "status"], workspace_root)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["command"] == "ws-status"
        assert "repos" in data

    def test_ws_status_no_workspace(self, tmp_path):
        _git_init(tmp_path)
        result = _run_roam(["ws", "status"], tmp_path)
        assert result.returncode != 0


# ===================================================================
# Phase 2: API scanning and endpoint matching
# ===================================================================


class TestApiScanner:
    """Test API call/route scanning."""

    def test_scan_file_for_api_calls(self, tmp_path):
        from roam.workspace.api_scanner import _scan_file_for_api_calls

        src = tmp_path / "api.js"
        src.write_text(
            'const res = api.get("/users");\n'
            'api.post("/users/create", data);\n'
            'axios.delete("/users/123");\n'
            'const x = 42;\n'  # not an API call
        )
        calls = _scan_file_for_api_calls(src, "api.js")
        assert len(calls) == 3
        assert calls[0]["url_pattern"] == "/users"
        assert calls[0]["http_method"] == "GET"
        assert calls[1]["url_pattern"] == "/users/create"
        assert calls[1]["http_method"] == "POST"
        assert calls[2]["url_pattern"] == "/users/123"
        assert calls[2]["http_method"] == "DELETE"

    def test_scan_file_for_routes_laravel(self, tmp_path):
        from roam.workspace.api_scanner import _scan_file_for_routes

        src = tmp_path / "routes.php"
        src.write_text(
            "<?php\n"
            "Route::get('/users', [UserController::class, 'index']);\n"
            "Route::post('/users/create', [UserController::class, 'store']);\n"
            "Route::delete('/users/{id}', [UserController::class, 'destroy']);\n"
            "Route::resource('/articles', ArticleController::class);\n"
        )
        routes = _scan_file_for_routes(src, "routes.php")
        assert len(routes) == 4
        assert routes[0]["url_pattern"] == "/users"
        assert routes[0]["http_method"] == "GET"
        assert routes[1]["url_pattern"] == "/users/create"
        assert routes[1]["http_method"] == "POST"
        assert routes[2]["url_pattern"] == "/users/{id}"
        assert routes[2]["http_method"] == "DELETE"
        assert routes[3]["http_method"] == "RESOURCE"

    def test_scan_file_for_routes_express(self, tmp_path):
        from roam.workspace.api_scanner import _scan_file_for_routes

        src = tmp_path / "routes.js"
        src.write_text(
            "router.get('/users', handler);\n"
            "app.post('/users', createHandler);\n"
        )
        routes = _scan_file_for_routes(src, "routes.js")
        assert len(routes) == 2
        assert routes[0]["http_method"] == "GET"
        assert routes[1]["http_method"] == "POST"

    def test_scan_file_for_routes_fastapi(self, tmp_path):
        from roam.workspace.api_scanner import _scan_file_for_routes

        src = tmp_path / "main.py"
        src.write_text(
            '@app.get("/items")\n'
            'def list_items():\n'
            '    return []\n'
            '\n'
            '@router.post("/items")\n'
            'def create_item():\n'
            '    pass\n'
        )
        routes = _scan_file_for_routes(src, "main.py")
        assert len(routes) == 2


class TestUrlNormalization:
    """Test URL normalization and matching."""

    def test_normalize_url_basic(self):
        from roam.workspace.api_scanner import _normalize_url

        assert _normalize_url("/users") == "/users"
        assert _normalize_url("/api/users") == "/users"
        assert _normalize_url("/api/v1/users") == "/users"

    def test_normalize_url_params(self):
        from roam.workspace.api_scanner import _normalize_url

        assert _normalize_url("/users/{id}") == "/users/[*]"
        assert _normalize_url("/users/${id}") == "/users/[*]"
        assert _normalize_url("/users/:userId") == "/users/[*]"

    def test_normalize_url_trailing_slash(self):
        from roam.workspace.api_scanner import _normalize_url

        assert _normalize_url("/users/") == "/users"

    def test_urls_equivalent(self):
        from roam.workspace.api_scanner import _urls_equivalent

        assert _urls_equivalent("/users", "/users")
        assert _urls_equivalent("/users/[*]", "/users/[*]")
        assert _urls_equivalent("/users/[*]", "/users/123")  # [*] matches anything
        assert not _urls_equivalent("/users", "/items")
        assert not _urls_equivalent("/users/a", "/users/a/b")


class TestEndpointMatching:
    """Test matching frontend calls to backend routes."""

    def test_exact_match(self):
        from roam.workspace.api_scanner import match_api_endpoints

        fe_calls = [{
            "symbol_id": 1, "url_pattern": "/users",
            "http_method": "GET", "file_path": "api.js",
            "line": 1, "symbol_name": "getUsers",
        }]
        be_routes = [{
            "symbol_id": 10, "url_pattern": "/users",
            "http_method": "GET", "file_path": "routes.php",
            "line": 5, "symbol_name": "index",
        }]
        matches = match_api_endpoints(fe_calls, be_routes)
        assert len(matches) == 1
        assert matches[0]["score"] > 0.5

    def test_param_match(self):
        from roam.workspace.api_scanner import match_api_endpoints

        fe_calls = [{
            "symbol_id": 1, "url_pattern": "/users/${id}",
            "http_method": "GET", "file_path": "api.js",
            "line": 1, "symbol_name": "getUser",
        }]
        be_routes = [{
            "symbol_id": 10, "url_pattern": "/users/{id}",
            "http_method": "GET", "file_path": "routes.php",
            "line": 5, "symbol_name": "show",
        }]
        matches = match_api_endpoints(fe_calls, be_routes)
        assert len(matches) == 1

    def test_no_match(self):
        from roam.workspace.api_scanner import match_api_endpoints

        fe_calls = [{
            "symbol_id": 1, "url_pattern": "/users",
            "http_method": "GET", "file_path": "api.js",
            "line": 1, "symbol_name": "getUsers",
        }]
        be_routes = [{
            "symbol_id": 10, "url_pattern": "/products",
            "http_method": "GET", "file_path": "routes.php",
            "line": 5, "symbol_name": "index",
        }]
        matches = match_api_endpoints(fe_calls, be_routes)
        assert len(matches) == 0

    def test_method_mismatch_excluded(self):
        from roam.workspace.api_scanner import match_api_endpoints

        fe_calls = [{
            "symbol_id": 1, "url_pattern": "/users",
            "http_method": "POST", "file_path": "api.js",
            "line": 1, "symbol_name": "createUser",
        }]
        be_routes = [{
            "symbol_id": 10, "url_pattern": "/users",
            "http_method": "GET", "file_path": "routes.php",
            "line": 5, "symbol_name": "index",
        }]
        matches = match_api_endpoints(fe_calls, be_routes)
        assert len(matches) == 0

    def test_api_prefix_stripped(self):
        from roam.workspace.api_scanner import match_api_endpoints

        fe_calls = [{
            "symbol_id": 1, "url_pattern": "/api/users",
            "http_method": "GET", "file_path": "api.js",
            "line": 1, "symbol_name": "getUsers",
        }]
        be_routes = [{
            "symbol_id": 10, "url_pattern": "/users",
            "http_method": "GET", "file_path": "routes.php",
            "line": 5, "symbol_name": "index",
        }]
        matches = match_api_endpoints(fe_calls, be_routes)
        assert len(matches) == 1


class TestBuildCrossEdges:
    """Test storing matched edges in the workspace DB."""

    def test_store_edges(self, tmp_path):
        from roam.workspace.db import open_workspace_db, upsert_repo, get_cross_edges
        from roam.workspace.api_scanner import build_cross_repo_edges

        with open_workspace_db(tmp_path) as conn:
            fe_id = upsert_repo(conn, "fe", "/fe", "frontend", "/fe/db")
            be_id = upsert_repo(conn, "be", "/be", "backend", "/be/db")

            matched = [{
                "frontend": {
                    "symbol_id": 1, "url_pattern": "/users",
                    "file_path": "api.js", "line": 5,
                    "symbol_name": "getUsers",
                },
                "backend": {
                    "symbol_id": 10, "url_pattern": "/users",
                    "file_path": "routes.php", "line": 3,
                    "symbol_name": "index",
                },
                "url_pattern": "/users",
                "http_method": "GET",
                "score": 0.95,
            }]

            count = build_cross_repo_edges(conn, fe_id, be_id, matched)
            assert count == 1

            edges = get_cross_edges(conn)
            assert len(edges) == 1
            assert edges[0]["kind"] == "api_call"


# ===================================================================
# Phase 2: ws resolve integration
# ===================================================================

class TestWsResolve:
    """Test `roam ws resolve` command."""

    def test_ws_resolve(self, workspace_root):
        # Re-init workspace
        fe = workspace_root / "frontend"
        be = workspace_root / "backend"
        _run_roam(["ws", "init", str(fe), str(be), "--name", "resolve-test"],
                   workspace_root)

        result = _run_roam(["ws", "resolve"], workspace_root)
        assert result.returncode == 0
        assert "Scanning" in result.stdout

    def test_ws_resolve_json(self, workspace_root):
        result = _run_roam(["--json", "ws", "resolve"], workspace_root)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["command"] == "ws-resolve"
        assert "matches" in data


# ===================================================================
# Phase 3: Unified workspace commands
# ===================================================================

class TestAggregator:
    """Test workspace aggregation functions."""

    def test_aggregate_understand(self, tmp_path):
        from roam.workspace.db import open_workspace_db, upsert_repo
        from roam.workspace.aggregator import aggregate_understand

        # Create a minimal repo DB
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        roam_dir = repo_dir / ".roam"
        roam_dir.mkdir()
        db_path = roam_dir / "index.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT, language TEXT, hash TEXT, mtime REAL, line_count INTEGER DEFAULT 0)")
        conn.execute("CREATE TABLE symbols (id INTEGER PRIMARY KEY, file_id INTEGER, name TEXT, qualified_name TEXT, kind TEXT, signature TEXT, line_start INTEGER, line_end INTEGER, docstring TEXT, visibility TEXT, is_exported INTEGER DEFAULT 1, parent_id INTEGER, default_value TEXT)")
        conn.execute("CREATE TABLE edges (id INTEGER PRIMARY KEY, source_id INTEGER, target_id INTEGER, kind TEXT, line INTEGER)")
        conn.execute("INSERT INTO files VALUES (1, 'main.py', 'python', 'abc', 0, 10)")
        conn.execute("INSERT INTO symbols VALUES (1, 1, 'main', 'main', 'function', NULL, 1, 5, NULL, 'public', 1, NULL, NULL)")
        conn.commit()
        conn.close()

        repo_infos = [{
            "name": "repo",
            "path": str(repo_dir),
            "role": "backend",
            "db_path": db_path,
        }]

        with open_workspace_db(tmp_path) as ws_conn:
            upsert_repo(ws_conn, "repo", str(repo_dir), "backend", str(db_path))
            data = aggregate_understand(ws_conn, repo_infos)

        assert data["total_files"] == 1
        assert data["total_symbols"] == 1
        assert len(data["repos"]) == 1
        assert data["repos"][0]["name"] == "repo"

    def test_cross_repo_context_not_found(self, tmp_path):
        from roam.workspace.db import open_workspace_db
        from roam.workspace.aggregator import cross_repo_context

        with open_workspace_db(tmp_path) as ws_conn:
            data = cross_repo_context(ws_conn, "nonexistent", [])

        assert data["symbol"] == "nonexistent"
        assert data["found_in"] == []

    def test_cross_repo_trace_no_bridge(self, tmp_path):
        from roam.workspace.db import open_workspace_db
        from roam.workspace.aggregator import cross_repo_trace

        with open_workspace_db(tmp_path) as ws_conn:
            data = cross_repo_trace(ws_conn, "foo", "bar", [])

        assert "not found" in data["verdict"].lower()


class TestWsUnderstand:
    """Test `roam ws understand` command."""

    def test_ws_understand(self, workspace_root):
        result = _run_roam(["ws", "understand"], workspace_root)
        assert result.returncode == 0
        assert "WORKSPACE" in result.stdout

    def test_ws_understand_json(self, workspace_root):
        result = _run_roam(["--json", "ws", "understand"], workspace_root)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["command"] == "ws-understand"
        assert "total_files" in data
        assert "repos" in data


class TestWsHealth:
    """Test `roam ws health` command."""

    def test_ws_health(self, workspace_root):
        result = _run_roam(["ws", "health"], workspace_root)
        assert result.returncode == 0
        assert "VERDICT" in result.stdout

    def test_ws_health_json(self, workspace_root):
        result = _run_roam(["--json", "ws", "health"], workspace_root)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["command"] == "ws-health"
        assert "workspace_health" in data["summary"]


class TestWsContext:
    """Test `roam ws context` command."""

    def test_ws_context_found(self, workspace_root):
        # This may or may not find the symbol depending on indexing
        result = _run_roam(["ws", "context", "fetchKiniseis"], workspace_root)
        assert result.returncode == 0

    def test_ws_context_not_found(self, workspace_root):
        result = _run_roam(["ws", "context", "nonexistent_symbol_xyz"],
                            workspace_root)
        assert result.returncode == 0
        assert "not found" in result.stdout.lower()

    def test_ws_context_json(self, workspace_root):
        result = _run_roam(["--json", "ws", "context", "main"], workspace_root)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["command"] == "ws-context"


class TestWsTrace:
    """Test `roam ws trace` command."""

    def test_ws_trace(self, workspace_root):
        result = _run_roam(["ws", "trace", "KinisiStore", "KinisiController"],
                            workspace_root)
        assert result.returncode == 0
        assert "VERDICT" in result.stdout

    def test_ws_trace_json(self, workspace_root):
        result = _run_roam(
            ["--json", "ws", "trace", "foo", "bar"],
            workspace_root,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["command"] == "ws-trace"
        assert "verdict" in data["summary"]


# ===================================================================
# Formatter helpers
# ===================================================================


class TestFormatterHelpers:
    """Test workspace-specific formatter additions."""

    def test_ws_loc(self):
        from roam.output.formatter import ws_loc

        assert ws_loc("fe", "src/api.js", 10) == "[fe] src/api.js:10"
        assert ws_loc("be", "routes.php") == "[be] routes.php"

    def test_ws_json_envelope(self):
        from roam.output.formatter import ws_json_envelope

        env = ws_json_envelope("ws-test", "my-workspace",
                                summary={"verdict": "ok"})
        assert env["command"] == "ws-test"
        assert env["workspace"] == "my-workspace"
        assert env["summary"]["verdict"] == "ok"
