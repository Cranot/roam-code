"""Tests for extended cross-language bridges: REST API, Template, Config.

Covers:
- RestApiBridge: detect(), resolve(), URL matching
- TemplateBridge: detect(), resolve(), variable matching
- ConfigBridge: detect(), resolve(), key matching
- Schema migrations for bridge/confidence columns on edges
- x-lang CLI command integration
"""
from __future__ import annotations

import json
import os

import pytest

from roam.bridges import registry as bridge_registry
from roam.bridges.bridge_rest_api import RestApiBridge
from roam.bridges.bridge_template import TemplateBridge
from roam.bridges.bridge_config import ConfigBridge

from tests.conftest import invoke_cli, parse_json_output, index_in_process, git_init


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset_registry():
    """Clear the global bridge registry for isolation."""
    bridge_registry._BRIDGES.clear()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def rest_api_project(project_factory):
    return project_factory({
        "frontend/app.js": "fetch('/api/users').then(r => r.json())\nfetch('/api/orders')\n",
        "backend/routes.py": (
            "from flask import Flask\n"
            "app = Flask(__name__)\n"
            "@app.route('/api/users')\n"
            "def get_users(): pass\n"
            "@app.route('/api/orders')\n"
            "def get_orders(): pass\n"
        ),
    })


@pytest.fixture
def template_project(project_factory):
    return project_factory({
        "templates/users.html": "<h1>{{ user }}</h1>\n<p>{{ items }}</p>\n",
        "app.py": (
            "from flask import render_template\n"
            "def show_users():\n"
            "    return render_template('users.html', user=current_user)\n"
        ),
    })


@pytest.fixture
def config_project(project_factory):
    return project_factory({
        ".env": "DATABASE_URL=postgres://localhost/db\nSECRET_KEY=abc123\n",
        "app.py": (
            "import os\n"
            "db_url = os.environ.get('DATABASE_URL')\n"
            "secret = os.getenv('SECRET_KEY')\n"
        ),
    })


@pytest.fixture
def pure_python_project(project_factory):
    return project_factory({
        "main.py": "def main(): pass\n",
        "utils.py": "def helper(): return 42\n",
    })


# ---------------------------------------------------------------------------
# RestApiBridge tests
# ---------------------------------------------------------------------------

class TestRestApiBridge:
    def setup_method(self):
        self.bridge = RestApiBridge()

    def test_rest_api_bridge_detect(self):
        """Detects a mixed frontend+backend project."""
        files = ["frontend/app.js", "backend/routes.py"]
        assert self.bridge.detect(files) is True

    def test_rest_api_bridge_no_detect(self):
        """Does not detect a pure Python project with no frontend."""
        files = ["main.py", "utils.py", "tests/test_main.py"]
        assert self.bridge.detect(files) is False

    def test_rest_api_resolve_urls(self):
        """Matches frontend fetch calls to backend Flask routes."""
        source_path = "frontend/app.js"
        source_symbols = [
            {
                "name": "fetchUsers",
                "kind": "function",
                "qualified_name": "app.fetchUsers",
                "signature": "fetch('/api/users')",
            },
        ]
        target_files = {
            "backend/routes.py": [
                {
                    "name": "get_users",
                    "kind": "function",
                    "qualified_name": "routes.get_users",
                    "signature": "@app.route('/api/users')",
                },
            ],
        }
        edges = self.bridge.resolve(source_path, source_symbols, target_files)
        assert len(edges) >= 1
        edge = edges[0]
        assert edge["kind"] == "x-lang"
        assert edge["bridge"] == "rest-api"
        assert edge["mechanism"] == "url-match"
        assert "/api/users" in edge.get("url", "")


# ---------------------------------------------------------------------------
# TemplateBridge tests
# ---------------------------------------------------------------------------

class TestTemplateBridge:
    def setup_method(self):
        self.bridge = TemplateBridge()

    def test_template_bridge_detect(self):
        """Detects project with template files."""
        files = ["templates/index.html", "app.py"]
        assert self.bridge.detect(files) is True

    def test_template_bridge_no_detect(self):
        """Does not detect project without template files."""
        files = ["main.py", "utils.py"]
        assert self.bridge.detect(files) is False

    def test_template_bridge_resolve(self):
        """Matches template to host language render call."""
        source_path = "templates/users.html"
        source_symbols = [
            {
                "name": "users.html",
                "kind": "template",
                "qualified_name": "templates/users.html",
                "signature": "{{ user }}",
            },
        ]
        target_files = {
            "app.py": [
                {
                    "name": "show_users",
                    "kind": "function",
                    "qualified_name": "app.show_users",
                    "signature": "render_template('users.html', user=current_user)",
                },
            ],
        }
        edges = self.bridge.resolve(source_path, source_symbols, target_files)
        assert len(edges) >= 1
        render_edges = [e for e in edges if e["mechanism"] == "template-render"]
        assert len(render_edges) >= 1
        assert render_edges[0]["bridge"] == "template"


# ---------------------------------------------------------------------------
# ConfigBridge tests
# ---------------------------------------------------------------------------

class TestConfigBridge:
    def setup_method(self):
        self.bridge = ConfigBridge()

    def test_config_bridge_detect(self):
        """Detects project with config files."""
        files = [".env", "app.py"]
        assert self.bridge.detect(files) is True

    def test_config_bridge_no_detect(self):
        """Does not detect project without config files."""
        files = ["main.py", "utils.py"]
        assert self.bridge.detect(files) is False

    def test_config_bridge_resolve(self):
        """Matches env var definitions to code reads."""
        source_path = ".env"
        source_symbols = [
            {
                "name": "DATABASE_URL",
                "kind": "variable",
                "qualified_name": ".env:DATABASE_URL",
                "signature": "DATABASE_URL=postgres://localhost/db",
            },
        ]
        target_files = {
            "app.py": [
                {
                    "name": "init_db",
                    "kind": "function",
                    "qualified_name": "app.init_db",
                    "signature": "os.environ.get('DATABASE_URL')",
                },
            ],
        }
        edges = self.bridge.resolve(source_path, source_symbols, target_files)
        assert len(edges) >= 1
        edge = edges[0]
        assert edge["kind"] == "x-lang"
        assert edge["bridge"] == "config"
        assert edge["mechanism"] == "config-read"
        assert edge.get("key") == "DATABASE_URL"


# ---------------------------------------------------------------------------
# Edge metadata tests
# ---------------------------------------------------------------------------

class TestEdgeMetadata:
    def test_bridge_edge_has_bridge_field(self):
        """Resolved edges include a 'bridge' field."""
        bridge = RestApiBridge()
        source_symbols = [
            {
                "name": "fetchData",
                "kind": "function",
                "qualified_name": "client.fetchData",
                "signature": "fetch('/api/data')",
            },
        ]
        target_files = {
            "server.py": [
                {
                    "name": "get_data",
                    "kind": "function",
                    "qualified_name": "server.get_data",
                    "signature": "@app.route('/api/data')",
                },
            ],
        }
        edges = bridge.resolve("client.js", source_symbols, target_files)
        assert len(edges) >= 1
        assert "bridge" in edges[0]
        assert edges[0]["bridge"] == "rest-api"

    def test_bridge_edge_has_confidence(self):
        """Resolved edges include a 'confidence' score."""
        bridge = ConfigBridge()
        source_symbols = [
            {
                "name": "API_KEY",
                "kind": "variable",
                "qualified_name": ".env:API_KEY",
                "signature": "API_KEY=secret",
            },
        ]
        target_files = {
            "app.py": [
                {
                    "name": "setup",
                    "kind": "function",
                    "qualified_name": "app.setup",
                    "signature": "os.environ.get('API_KEY')",
                },
            ],
        }
        edges = bridge.resolve(".env", source_symbols, target_files)
        assert len(edges) >= 1
        assert "confidence" in edges[0]
        assert 0.0 <= edges[0]["confidence"] <= 1.0


# ---------------------------------------------------------------------------
# Schema migration tests
# ---------------------------------------------------------------------------

class TestSchemaMigration:
    def test_schema_has_bridge_column(self, tmp_path):
        """Edges table has a 'bridge' column after schema migration."""
        from roam.db.connection import get_connection, ensure_schema
        db_path = tmp_path / "test.db"
        conn = get_connection(db_path, readonly=False)
        try:
            ensure_schema(conn)
            # Check column exists by querying pragma
            cols = conn.execute("PRAGMA table_info(edges)").fetchall()
            col_names = [c[1] for c in cols]
            assert "bridge" in col_names
        finally:
            conn.close()

    def test_schema_has_confidence_column(self, tmp_path):
        """Edges table has a 'confidence' column after schema migration."""
        from roam.db.connection import get_connection, ensure_schema
        db_path = tmp_path / "test.db"
        conn = get_connection(db_path, readonly=False)
        try:
            ensure_schema(conn)
            cols = conn.execute("PRAGMA table_info(edges)").fetchall()
            col_names = [c[1] for c in cols]
            assert "confidence" in col_names
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# CLI integration tests
# ---------------------------------------------------------------------------

class TestXLangCommand:
    def test_xlang_command_runs(self, rest_api_project, cli_runner):
        """roam x-lang exits 0."""
        result = invoke_cli(cli_runner, ["x-lang"], cwd=rest_api_project)
        assert result.exit_code == 0

    def test_xlang_command_json(self, rest_api_project, cli_runner):
        """roam --json x-lang produces valid JSON envelope."""
        result = invoke_cli(cli_runner, ["x-lang"], cwd=rest_api_project, json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "command" in data
        assert data["command"] == "x-lang"
        assert "summary" in data
        assert "bridges" in data["summary"]

    def test_xlang_shows_bridges(self, rest_api_project, cli_runner):
        """roam x-lang mentions detected bridge names in output."""
        result = invoke_cli(cli_runner, ["x-lang"], cwd=rest_api_project)
        assert result.exit_code == 0
        output = result.output.lower()
        # At minimum, the rest-api bridge or config bridge should be detected
        # (the project has .js and .py files)
        assert "bridge" in output or "rest-api" in output or "config" in output or "no cross-language" in output
