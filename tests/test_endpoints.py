"""Tests for the ``roam endpoints`` command.

Covers:
1. Flask route detection (@app.route, @app.get, @app.post)
2. FastAPI route detection (@app.get, @router.post)
3. Django path() / url() detection
4. Express.js route detection (app.get, router.post)
5. Go net/http HandleFunc detection
6. Java Spring @GetMapping / @PostMapping detection
7. Laravel Route:: detection
8. GraphQL Query/Mutation field detection
9. gRPC service/rpc detection
10. JSON output format and envelope structure
11. --framework filter
12. --method filter
13. --group-by option
14. Empty project (no endpoints found)
15. Mixed project (multiple frameworks)
16. Verdict-first text output
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, git_commit, index_in_process

from roam.cli import cli


# ===========================================================================
# Helpers
# ===========================================================================


def _make_project(tmp_path: Path, file_dict: dict[str, str]) -> Path:
    """Create a project directory with specified files and a git repo."""
    proj = tmp_path / "proj"
    proj.mkdir(exist_ok=True)
    for rel, content in file_dict.items():
        fp = proj / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
    git_init(str(proj))
    return proj


def _index_project(proj: Path) -> None:
    """Run roam index in the project directory."""
    _, exit_code = index_in_process(str(proj))
    # Exit code 0 or 1 (partial) are both acceptable for test projects
    assert exit_code in (0, 1), f"index failed with exit code {exit_code}"


def _invoke(proj: Path, *args, json_mode: bool = False) -> object:
    """Run ``roam endpoints`` in-process via CliRunner."""
    runner = CliRunner()
    full_args = []
    if json_mode:
        full_args.append("--json")
    full_args.append("endpoints")
    full_args.extend(args)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        result = runner.invoke(cli, full_args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


# ===========================================================================
# Sample source files
# ===========================================================================

FLASK_APP = '''\
from flask import Flask

app = Flask(__name__)


@app.route('/api/users', methods=['GET'])
def get_users():
    return []


@app.route('/api/users', methods=['POST'])
def create_user():
    return {}, 201


@app.get('/api/users/<int:user_id>')
def get_user(user_id):
    return {}


@app.delete('/api/users/<int:user_id>')
def delete_user(user_id):
    return '', 204
'''

FASTAPI_APP = '''\
from fastapi import FastAPI, APIRouter

app = FastAPI()
router = APIRouter()


@app.get('/health')
def health_check():
    return {"status": "ok"}


@router.post('/items')
async def create_item(item: dict):
    return item


@router.put('/items/{item_id}')
async def update_item(item_id: int, item: dict):
    return item
'''

DJANGO_URLS = '''\
from django.urls import path, include
from . import views

urlpatterns = [
    path('/api/users/', views.UserListView.as_view()),
    path('/api/users/<int:pk>/', views.UserDetailView.as_view()),
    path('/api/posts/', views.PostListView.as_view()),
]
'''

EXPRESS_APP = '''\
const express = require('express');
const app = express();
const router = express.Router();

app.get('/api/products', getProducts);
app.post('/api/products', createProduct);
router.put('/api/products/:id', updateProduct);
router.delete('/api/products/:id', deleteProduct);

app.use('/api', router);
'''

GO_HTTP = '''\
package main

import (
    "net/http"
)

func main() {
    http.HandleFunc("/api/users", handleUsers)
    http.HandleFunc("/api/health", handleHealth)
    http.ListenAndServe(":8080", nil)
}

func handleUsers(w http.ResponseWriter, r *http.Request) {}
func handleHealth(w http.ResponseWriter, r *http.Request) {}
'''

SPRING_CONTROLLER = '''\
package com.example.api;

import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/orders")
public class OrderController {

    @GetMapping("")
    public List<Order> listOrders() {
        return orderService.findAll();
    }

    @PostMapping("")
    public Order createOrder(@RequestBody Order order) {
        return orderService.save(order);
    }

    @GetMapping("/{id}")
    public Order getOrder(@PathVariable Long id) {
        return orderService.findById(id);
    }

    @DeleteMapping("/{id}")
    public void deleteOrder(@PathVariable Long id) {
        orderService.delete(id);
    }
}
'''

LARAVEL_ROUTES = '''\
<?php

use Illuminate\\Support\\Facades\\Route;
use App\\Http\\Controllers\\UserController;
use App\\Http\\Controllers\\PostController;

Route::get('/api/users', [UserController::class, 'index']);
Route::post('/api/users', [UserController::class, 'store']);
Route::get('/api/users/{id}', [UserController::class, 'show']);
Route::put('/api/users/{id}', [UserController::class, 'update']);
Route::delete('/api/users/{id}', [UserController::class, 'destroy']);
Route::get('/api/posts', PostController::class);
'''

GRAPHQL_SCHEMA = '''\
type Query {
    users: [User!]!
    user(id: ID!): User
    posts(userId: ID): [Post!]!
}

type Mutation {
    createUser(input: CreateUserInput!): User!
    updateUser(id: ID!, input: UpdateUserInput!): User!
    deleteUser(id: ID!): Boolean!
}

type Subscription {
    userCreated: User!
}

type User {
    id: ID!
    name: String!
    email: String!
}
'''

GRPC_PROTO = '''\
syntax = "proto3";

package user;

service UserService {
    rpc GetUser(GetUserRequest) returns (User);
    rpc ListUsers(ListUsersRequest) returns (ListUsersResponse);
    rpc CreateUser(CreateUserRequest) returns (User);
    rpc DeleteUser(DeleteUserRequest) returns (Empty);
}

message User {
    string id = 1;
    string name = 2;
    string email = 3;
}
'''

EMPTY_PY = '''\
# No routes here
x = 1
y = 2
'''


# ===========================================================================
# 1. Flask route detection
# ===========================================================================

class TestFlaskDetection:
    def test_flask_routes_detected(self, tmp_path):
        proj = _make_project(tmp_path, {"app.py": FLASK_APP})
        _index_project(proj)
        result = _invoke(proj)
        assert result.exit_code == 0
        assert "VERDICT:" in result.output
        assert "/api/users" in result.output

    def test_flask_methods_detected(self, tmp_path):
        proj = _make_project(tmp_path, {"app.py": FLASK_APP})
        _index_project(proj)
        result = _invoke(proj)
        assert "GET" in result.output
        assert "POST" in result.output
        assert "DELETE" in result.output

    def test_flask_handler_names(self, tmp_path):
        proj = _make_project(tmp_path, {"app.py": FLASK_APP})
        _index_project(proj)
        result = _invoke(proj)
        assert "get_users" in result.output or "create_user" in result.output

    def test_flask_framework_label(self, tmp_path):
        proj = _make_project(tmp_path, {"app.py": FLASK_APP})
        _index_project(proj)
        result = _invoke(proj)
        output_lower = result.output.lower()
        assert "flask" in output_lower or "fastapi" in output_lower or "python" in output_lower


# ===========================================================================
# 2. FastAPI route detection
# ===========================================================================

class TestFastAPIDetection:
    def test_fastapi_routes_detected(self, tmp_path):
        proj = _make_project(tmp_path, {"main.py": FASTAPI_APP})
        _index_project(proj)
        result = _invoke(proj)
        assert result.exit_code == 0
        assert "/health" in result.output or "/items" in result.output

    def test_fastapi_json(self, tmp_path):
        proj = _make_project(tmp_path, {"main.py": FASTAPI_APP})
        _index_project(proj)
        result = _invoke(proj, json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        ep_list = data.get("endpoints", [])
        paths = [e["path"] for e in ep_list]
        assert any("/health" == p or "/items" == p for p in paths)


# ===========================================================================
# 3. Django detection
# ===========================================================================

class TestDjangoDetection:
    def test_django_paths_detected(self, tmp_path):
        proj = _make_project(tmp_path, {"urls.py": DJANGO_URLS})
        _index_project(proj)
        result = _invoke(proj)
        assert result.exit_code == 0
        # Django path entries should be found
        assert "/api/users/" in result.output or "UserListView" in result.output or "ANY" in result.output


# ===========================================================================
# 4. Express.js route detection
# ===========================================================================

class TestExpressDetection:
    def test_express_routes_detected(self, tmp_path):
        proj = _make_project(tmp_path, {"server.js": EXPRESS_APP})
        _index_project(proj)
        result = _invoke(proj)
        assert result.exit_code == 0
        assert "/api/products" in result.output

    def test_express_methods(self, tmp_path):
        proj = _make_project(tmp_path, {"server.js": EXPRESS_APP})
        _index_project(proj)
        result = _invoke(proj)
        assert "GET" in result.output
        assert "POST" in result.output

    def test_express_framework_label(self, tmp_path):
        proj = _make_project(tmp_path, {"server.js": EXPRESS_APP})
        _index_project(proj)
        result = _invoke(proj)
        assert "express" in result.output.lower() or "javascript" in result.output.lower()


# ===========================================================================
# 5. Go net/http detection
# ===========================================================================

class TestGoDetection:
    def test_go_handlefunc_detected(self, tmp_path):
        proj = _make_project(tmp_path, {"main.go": GO_HTTP})
        _index_project(proj)
        result = _invoke(proj)
        assert result.exit_code == 0
        assert "/api/users" in result.output or "/api/health" in result.output

    def test_go_handler_name(self, tmp_path):
        proj = _make_project(tmp_path, {"main.go": GO_HTTP})
        _index_project(proj)
        result = _invoke(proj)
        assert "handleUsers" in result.output or "handleHealth" in result.output


# ===========================================================================
# 6. Java Spring detection
# ===========================================================================

class TestSpringDetection:
    def test_spring_mappings_detected(self, tmp_path):
        proj = _make_project(tmp_path, {"OrderController.java": SPRING_CONTROLLER})
        _index_project(proj)
        result = _invoke(proj)
        assert result.exit_code == 0
        assert "/api/orders" in result.output

    def test_spring_methods(self, tmp_path):
        proj = _make_project(tmp_path, {"OrderController.java": SPRING_CONTROLLER})
        _index_project(proj)
        result = _invoke(proj)
        assert "GET" in result.output
        assert "POST" in result.output
        assert "DELETE" in result.output

    def test_spring_framework_label(self, tmp_path):
        proj = _make_project(tmp_path, {"OrderController.java": SPRING_CONTROLLER})
        _index_project(proj)
        result = _invoke(proj)
        assert "spring" in result.output.lower()


# ===========================================================================
# 7. Laravel detection
# ===========================================================================

class TestLaravelDetection:
    def test_laravel_routes_detected(self, tmp_path):
        proj = _make_project(tmp_path, {"routes/api.php": LARAVEL_ROUTES})
        _index_project(proj)
        result = _invoke(proj)
        assert result.exit_code == 0
        assert "/api/users" in result.output

    def test_laravel_controller_handler(self, tmp_path):
        proj = _make_project(tmp_path, {"routes/api.php": LARAVEL_ROUTES})
        _index_project(proj)
        result = _invoke(proj)
        assert "UserController" in result.output or "index" in result.output


# ===========================================================================
# 8. GraphQL detection
# ===========================================================================

class TestGraphQLDetection:
    def test_graphql_queries_detected(self, tmp_path):
        proj = _make_project(tmp_path, {"schema.graphql": GRAPHQL_SCHEMA})
        _index_project(proj)
        result = _invoke(proj)
        assert result.exit_code == 0
        assert "QUERY" in result.output or "graphql" in result.output.lower()
        assert "users" in result.output or "user" in result.output

    def test_graphql_mutations_detected(self, tmp_path):
        proj = _make_project(tmp_path, {"schema.graphql": GRAPHQL_SCHEMA})
        _index_project(proj)
        result = _invoke(proj)
        assert "MUTATION" in result.output or "createUser" in result.output

    def test_graphql_json(self, tmp_path):
        proj = _make_project(tmp_path, {"schema.graphql": GRAPHQL_SCHEMA})
        _index_project(proj)
        result = _invoke(proj, json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        ep_list = data.get("endpoints", [])
        gql_eps = [e for e in ep_list if e.get("framework") == "graphql"]
        assert len(gql_eps) > 0
        methods = {e["method"] for e in gql_eps}
        assert "QUERY" in methods or "MUTATION" in methods


# ===========================================================================
# 9. gRPC detection
# ===========================================================================

class TestGRPCDetection:
    def test_grpc_rpc_detected(self, tmp_path):
        proj = _make_project(tmp_path, {"user.proto": GRPC_PROTO})
        _index_project(proj)
        result = _invoke(proj)
        assert result.exit_code == 0
        assert "RPC" in result.output or "grpc" in result.output.lower()
        assert "GetUser" in result.output or "UserService" in result.output

    def test_grpc_json(self, tmp_path):
        proj = _make_project(tmp_path, {"user.proto": GRPC_PROTO})
        _index_project(proj)
        result = _invoke(proj, json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        ep_list = data.get("endpoints", [])
        grpc_eps = [e for e in ep_list if e.get("framework") == "grpc"]
        assert len(grpc_eps) > 0
        assert all(e["method"] == "RPC" for e in grpc_eps)


# ===========================================================================
# 10. JSON output format
# ===========================================================================

class TestJsonOutput:
    def test_json_envelope_structure(self, tmp_path):
        """JSON output follows the standard roam envelope contract."""
        proj = _make_project(tmp_path, {"app.py": FLASK_APP})
        _index_project(proj)
        result = _invoke(proj, json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        # Standard envelope fields
        assert "schema" in data
        assert "schema_version" in data
        assert "command" in data
        assert data["command"] == "endpoints"
        assert "summary" in data
        assert "verdict" in data["summary"]
        assert "_meta" in data
        assert "timestamp" in data["_meta"]

    def test_json_summary_fields(self, tmp_path):
        """Summary contains count, frameworks, framework_count."""
        proj = _make_project(tmp_path, {"app.py": FLASK_APP})
        _index_project(proj)
        result = _invoke(proj, json_mode=True)
        data = json.loads(result.output)
        summary = data["summary"]
        assert "count" in summary
        assert "frameworks" in summary
        assert "framework_count" in summary
        assert isinstance(summary["count"], int)
        assert summary["count"] > 0

    def test_json_endpoints_array(self, tmp_path):
        """Each endpoint has required fields."""
        proj = _make_project(tmp_path, {"app.py": FLASK_APP})
        _index_project(proj)
        result = _invoke(proj, json_mode=True)
        data = json.loads(result.output)
        ep_list = data.get("endpoints", [])
        assert len(ep_list) > 0
        for ep in ep_list:
            assert "method" in ep
            assert "path" in ep
            assert "handler" in ep
            assert "file" in ep
            assert "line" in ep
            assert "framework" in ep
            assert isinstance(ep["line"], int)

    def test_json_empty_project(self, tmp_path):
        """Empty project returns count=0 and verdict indicating no endpoints."""
        proj = _make_project(tmp_path, {"utils.py": EMPTY_PY})
        _index_project(proj)
        result = _invoke(proj, json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["summary"]["count"] == 0
        assert "no endpoint" in data["summary"]["verdict"].lower()
        assert data.get("endpoints") == [] or data.get("endpoints") is None or \
               len(data.get("endpoints", [])) == 0


# ===========================================================================
# 11. --framework filter
# ===========================================================================

class TestFrameworkFilter:
    def test_filter_flask(self, tmp_path):
        """--framework flask shows only flask endpoints."""
        proj = _make_project(tmp_path, {
            "app.py": FLASK_APP,
            "server.js": EXPRESS_APP,
        })
        _index_project(proj)
        result = _invoke(proj, "--framework", "flask", json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        ep_list = data.get("endpoints", [])
        frameworks = {e["framework"] for e in ep_list}
        assert all("flask" in f.lower() or "fastapi" in f.lower() or "python" in f.lower()
                   for f in frameworks), f"Unexpected frameworks: {frameworks}"

    def test_filter_express(self, tmp_path):
        """--framework express shows only express endpoints."""
        proj = _make_project(tmp_path, {
            "app.py": FLASK_APP,
            "server.js": EXPRESS_APP,
        })
        _index_project(proj)
        result = _invoke(proj, "--framework", "express", json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        ep_list = data.get("endpoints", [])
        frameworks = {e["framework"] for e in ep_list}
        assert all("express" in f.lower() or "javascript" in f.lower()
                   for f in frameworks), f"Unexpected frameworks: {frameworks}"

    def test_filter_no_match(self, tmp_path):
        """--framework with no matches returns count=0."""
        proj = _make_project(tmp_path, {"app.py": FLASK_APP})
        _index_project(proj)
        result = _invoke(proj, "--framework", "grpc", json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["summary"]["count"] == 0


# ===========================================================================
# 12. --method filter
# ===========================================================================

class TestMethodFilter:
    def test_filter_get_method(self, tmp_path):
        """--method GET shows only GET endpoints."""
        proj = _make_project(tmp_path, {"app.py": FLASK_APP})
        _index_project(proj)
        result = _invoke(proj, "--method", "GET", json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        ep_list = data.get("endpoints", [])
        assert all(e["method"] == "GET" for e in ep_list), \
            f"Non-GET methods found: {[e['method'] for e in ep_list]}"

    def test_filter_post_method(self, tmp_path):
        """--method POST shows only POST endpoints."""
        proj = _make_project(tmp_path, {"app.py": FLASK_APP})
        _index_project(proj)
        result = _invoke(proj, "--method", "POST", json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        ep_list = data.get("endpoints", [])
        assert all(e["method"] == "POST" for e in ep_list)

    def test_filter_method_no_match(self, tmp_path):
        """--method PATCH on Flask-only app with no PATCH routes returns 0."""
        proj = _make_project(tmp_path, {"app.py": FLASK_APP})
        _index_project(proj)
        result = _invoke(proj, "--method", "PATCH", json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["summary"]["count"] == 0


# ===========================================================================
# 13. --group-by option
# ===========================================================================

class TestGroupBy:
    def test_group_by_framework(self, tmp_path):
        """Default group-by=framework groups by framework name."""
        proj = _make_project(tmp_path, {"app.py": FLASK_APP})
        _index_project(proj)
        result = _invoke(proj)
        assert result.exit_code == 0
        # Should have a group header (===)
        assert "===" in result.output

    def test_group_by_file(self, tmp_path):
        """--group-by file groups by file path."""
        proj = _make_project(tmp_path, {
            "app.py": FLASK_APP,
            "server.js": EXPRESS_APP,
        })
        _index_project(proj)
        result = _invoke(proj, "--group-by", "file")
        assert result.exit_code == 0
        assert "===" in result.output
        # File names should appear as group headers
        assert "app.py" in result.output or "server.js" in result.output

    def test_group_by_method(self, tmp_path):
        """--group-by method groups by HTTP method."""
        proj = _make_project(tmp_path, {"app.py": FLASK_APP})
        _index_project(proj)
        result = _invoke(proj, "--group-by", "method")
        assert result.exit_code == 0
        assert "===" in result.output
        assert "GET" in result.output or "POST" in result.output


# ===========================================================================
# 14. Empty project
# ===========================================================================

class TestEmptyProject:
    def test_no_endpoints_text(self, tmp_path):
        """Text output says no endpoints found when none exist."""
        proj = _make_project(tmp_path, {"utils.py": EMPTY_PY})
        _index_project(proj)
        result = _invoke(proj)
        assert result.exit_code == 0
        assert "VERDICT:" in result.output
        assert "no endpoint" in result.output.lower()

    def test_no_endpoints_json(self, tmp_path):
        proj = _make_project(tmp_path, {"utils.py": EMPTY_PY})
        _index_project(proj)
        result = _invoke(proj, json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["summary"]["count"] == 0


# ===========================================================================
# 15. Mixed project (multiple frameworks)
# ===========================================================================

class TestMixedProject:
    def test_multi_framework_count(self, tmp_path):
        """Multiple framework files contribute separate endpoint groups."""
        proj = _make_project(tmp_path, {
            "app.py": FLASK_APP,
            "server.js": EXPRESS_APP,
        })
        _index_project(proj)
        result = _invoke(proj, json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["summary"]["count"] >= 2
        assert data["summary"]["framework_count"] >= 1

    def test_multi_framework_text(self, tmp_path):
        proj = _make_project(tmp_path, {
            "app.py": FLASK_APP,
            "server.js": EXPRESS_APP,
        })
        _index_project(proj)
        result = _invoke(proj)
        assert result.exit_code == 0
        assert "VERDICT:" in result.output
        # Endpoints from both frameworks appear
        assert "/api/users" in result.output or "/api/products" in result.output

    def test_graphql_and_rest(self, tmp_path):
        """GraphQL and REST endpoints can coexist."""
        proj = _make_project(tmp_path, {
            "app.py": FLASK_APP,
            "schema.graphql": GRAPHQL_SCHEMA,
        })
        _index_project(proj)
        result = _invoke(proj, json_mode=True)
        data = json.loads(result.output)
        frameworks = data["summary"]["frameworks"]
        assert len(frameworks) >= 1
        assert data["summary"]["count"] > 0


# ===========================================================================
# 16. Verdict-first text output
# ===========================================================================

class TestVerdictFirst:
    def test_verdict_is_first_line(self, tmp_path):
        """VERDICT: is the first non-empty line of text output."""
        proj = _make_project(tmp_path, {"app.py": FLASK_APP})
        _index_project(proj)
        result = _invoke(proj)
        assert result.exit_code == 0
        lines = [l for l in result.output.splitlines() if l.strip()]
        assert lines[0].startswith("VERDICT:"), \
            f"First line is not VERDICT: — got: {lines[0]!r}"

    def test_verdict_contains_count(self, tmp_path):
        """VERDICT line mentions endpoint count."""
        proj = _make_project(tmp_path, {"app.py": FLASK_APP})
        _index_project(proj)
        result = _invoke(proj)
        first_line = result.output.splitlines()[0]
        # Should contain a number and "endpoint"
        assert "endpoint" in first_line.lower()

    def test_verdict_empty_project(self, tmp_path):
        """VERDICT line says no endpoints when none detected."""
        proj = _make_project(tmp_path, {"utils.py": EMPTY_PY})
        _index_project(proj)
        result = _invoke(proj)
        lines = [l for l in result.output.splitlines() if l.strip()]
        assert lines[0].startswith("VERDICT:")
        assert "no endpoint" in lines[0].lower()


# ===========================================================================
# 17. File/line number accuracy
# ===========================================================================

class TestFileLineInfo:
    def test_flask_file_reference(self, tmp_path):
        """Each endpoint references the correct source file."""
        proj = _make_project(tmp_path, {"myapp/api.py": FLASK_APP})
        _index_project(proj)
        result = _invoke(proj, json_mode=True)
        data = json.loads(result.output)
        ep_list = data.get("endpoints", [])
        flask_eps = [e for e in ep_list
                     if "flask" in e.get("framework", "").lower()
                     or "fastapi" in e.get("framework", "").lower()
                     or "python" in e.get("framework", "").lower()]
        if flask_eps:
            for ep in flask_eps:
                assert "api.py" in ep["file"]
                assert isinstance(ep["line"], int)
                assert ep["line"] >= 1

    def test_go_line_numbers(self, tmp_path):
        """Go HandleFunc endpoints have valid line numbers."""
        proj = _make_project(tmp_path, {"main.go": GO_HTTP})
        _index_project(proj)
        result = _invoke(proj, json_mode=True)
        data = json.loads(result.output)
        ep_list = data.get("endpoints", [])
        go_eps = [e for e in ep_list if "go" in e.get("framework", "").lower()
                  or "net/http" in e.get("framework", "").lower()]
        for ep in go_eps:
            assert ep["line"] >= 1


# ===========================================================================
# 18. Test files are excluded by default
# ===========================================================================

class TestFileExclusion:
    def test_test_files_excluded(self, tmp_path):
        """Route definitions in test files are not reported by default."""
        proj = _make_project(tmp_path, {
            "app.py": FLASK_APP,
            "tests/test_app.py": '''\
import pytest
from app import app

def test_get_users():
    # This simulates calling app.get('/test/route', ...) in a test helper
    pass
''',
        })
        _index_project(proj)
        result = _invoke(proj, json_mode=True)
        data = json.loads(result.output)
        ep_list = data.get("endpoints", [])
        for ep in ep_list:
            assert "test_app" not in ep["file"]

    def test_include_tests_flag(self, tmp_path):
        """--include-tests expands the search to test files."""
        # Just verify the flag is accepted without error
        proj = _make_project(tmp_path, {"app.py": FLASK_APP})
        _index_project(proj)
        result = _invoke(proj, "--include-tests")
        assert result.exit_code == 0
